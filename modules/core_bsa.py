import numpy as np
import pandas as pd
import statsmodels.api as sm
import warnings
from plotnine import (
    ggplot, aes, geom_point, geom_line, theme_linedraw,
    facet_grid, theme, ggtitle, xlab, ylab, geom_hline
)
from multiprocessing import Pool

from modules.utilities_logging import LogHandler


"""
Core module for bsa analysis
Input variable class: BSA_variables from utilities_variables module.
The read-depth analysis between the wild-type and mutant bulks are stored here.
"""
    
class BSA:
    def __init__(self, logger, bsa_vars):
        #AnalysisVariables class passed to function. 
        self.log = logger
        
        self.bsa_vars = bsa_vars

    def run_pipeline(self):
        smoothing_function = sm.nonparametric.lowess

        for line in self.bsa_vars.lines:
            self.log = LogHandler(f'analysis_{line.name}')
            self.log.add_db_record(line, self.log.ulid, line.vcf_ulid)
            
            line.analysis_ulid = self.log.ulid 

            #Load VCF data table pandas dataframe vcf_df
            line.vcf_df = self.bsa_vars.load_vcf_table(line.vcf_table_path) #vcf_table_path generated in modules.core_vcf_gen or modules.utilities_lines
            
            ## data cleaning and orginization
            data_filter = DataFiltering(self.log, line.name)
            line.vcf_df = data_filter.filter_genotypes(line.segregation_type, line.vcf_df)
            
            
            if self.bsa_vars.filter_indels: 
                line.vcf_df = data_filter.drop_indels(line.vcf_df)
                
            if self.bsa_vars.filter_ems: #for EMS mutants
                line.vcf_df = data_filter.filter_ems_mutations(line.vcf_df)
                            
            if self.bsa_vars.snpmask_path: #Mask background snps if provided
                line.snpmask_df = self.bsa_vars.load_snpmask(self.bsa_vars.snpmask_path)
                line.vcf_df = data_filter.mask_known_snps(line.snpmask_df, line.vcf_df)
                
            
            ## Feature production
            feature_prod = FeatureProduction(self.log, line.name)
            line.vcf_df = feature_prod.calculate_delta_snp_and_g_statistic(line.vcf_df)
            
            line.vcf_df = data_filter.drop_na(line.vcf_df)
            line.vcf_df = data_filter.drop_genos_with_negative_ratios(line.vcf_df)
            
            line.vcf_df = feature_prod.fit_model(
                line.vcf_df, smoothing_function, self.bsa_vars.loess_span, 
                self.bsa_vars.smooth_edges_bounds
            )
            
            ### Use bootstrapping to produce cutoffs for features
            cutoffs = feature_prod.calculate_empirical_cutoffs(
                line.vcf_df, smoothing_function, self.bsa_vars.loess_span, 
                self.bsa_vars.shuffle_iterations
            )
            line.gs_cutoff, line.rs_cutoff, line.rsg_cutoff, line.rsg_y_cutoff = cutoffs
            
            line.vcf_df = feature_prod.label_df_with_cutoffs(
                line.vcf_df, line.gs_cutoff, line.rsg_cutoff, line.rsg_y_cutoff
            )
            
            #Construct output file path prefix
            line.analysis_out_prefix = self.bsa_vars.gen_bsa_out_prefix(
                line.name, line.analysis_ulid, line.vcf_ulid
            )

            ## Saving and plotting outputs
            table_and_plots = TableAndPlots(
                self.log,
                line.name,
                line.vcf_df,
                line.analysis_out_prefix
            )
            table_and_plots.sort_save_likely_candidates()
            table_and_plots.generate_plots(
                line.gs_cutoff, 
                line.rsg_cutoff, 
                line.rsg_y_cutoff
            )


class DataFiltering:
    def __init__ (self, logger, name):
        self.log = logger
        
        self.name = name
    

    def drop_indels(self, vcf_df: pd.DataFrame)-> pd.DataFrame:
        """
        Drops insertion/deletions from VCF dataframe.
        
        Args: 
        vcf_df(pd.DataFrame)
        VCF dataframe
        
        Returns: 
        VCF dataframe with no indels
        """
        self.log.attempt('Attempting to drop indels...')
        try:
            vcf_df = vcf_df.loc[~(vcf_df["ref"].str.len() > 1) 
                & ~(vcf_df["alt"].str.len() > 1)
            ]
            self.log.success("Indels dropped")
            return vcf_df
        
        except AttributeError:
            self.log.fail("'ref' and 'alt' columns should only contain strings. VCF may not be properly formatted. Aborting...")
        except KeyError:
            self.log.fail("'ref' or 'alt' column not found in the DataFrame. Please ensure they exist.")
    

    def drop_na(self, vcf_df: pd.DataFrame)-> pd.DataFrame:
        """
        Drops rows with NaN values from VCF dataframe.
        
        Args: 
            vcf_df(pd.DataFrame)
            VCF dataframe
        
        Produces: 
            VCF dataframe with no NaN values
        """
        self.log.attempt('Attempting to drop NaN values...')
        vcf_df = vcf_df.dropna(axis=0, how='any', subset=["ratio"])
        self.log.success('NaN values dropped')
        
        return vcf_df


    def filter_genotypes(self, segregation_type: str, vcf_df: pd.DataFrame)-> pd.DataFrame:
        """
        Filter genotypes in the 'mu:wt_GTpred' column of a DataFrame based on 
        the specified allele.
        
        Args:
            segregation_type (str): The allele value to filter the genotypes. 
                Filters: 
                        R = Recessive seg '1/1:0/1', '0/1:0/0', '0/1:0/1'.
                        D = Dominant seg '0/1:0/0', '1/1:0/0', '0/1:0/1'.
            
            vcf_df (pd.DataFrame): The input DataFrame containing the genotypes.

        Returns:
            pd.DataFrame: Filtered DataFrame containing only the rows with 
            matching genotypes.

        [EXTRA INFO]
        0/1:0/1 is included because of occasianal leaky genotypying by GATK 
        haplotype caller. Nearly 100% of negative delta SNP values arise from 
        0/1:0/1 situations. To retain information without losing data that
        may help fit GAM or LOESS, we will retain 0/1:0/1 loci and instead 
        cut the negative values out after calculating the delta allele
        see: self.drop_genos_with_negative_ratios
        
        """
        self.log.attempt('Attempting to filter genotypes based on segregation pattern...')
        try:
            if segregation_type == 'R':
                self.log.note('Filtering genotypes based an a recessive segregation pattern')
                seg_filter = ['1/1:0/1', '0/1:0/0','0/1:0/1']
            elif segregation_type == 'D':
                self.log.note('Filtering genotypes based an a dominant segregation pattern')
                seg_filter = ['0/1:0/0', '1/1:0/0','0/1:0/1']  
            else: 
                self.log.fail(f'Allele type:{segregation_type} is not a valid selection! Aborting.')
            
            try:
                vcf_df = vcf_df[vcf_df['mu:wt_GTpred'].isin(seg_filter)]
                
                self.log.success('Genotypes filtured based on segregation pattern')
                
                return vcf_df

            except KeyError as e:
                self.log.note('Key error. VCF dataframe should have the following headers: ')
                self.log.note('chr  pos ref alt gene snpEffect snpVariant snpImpact mu:wt_GTpred mu_ref mu_alt wt_ref wt_alt')
                self.log.fail(f"Dataframe doesn't contain {e} column. Aborting...")
            
        
        except Exception as e:
            self.log.fail(f'There was an error while filtering genotypes:{e}')        


    def filter_ems_mutations(self, vcf_df: pd.DataFrame)-> pd.DataFrame:
        """
        Filter mutations likely to be from EMS for analysis and return a filtered DataFrame.

        Args:
            vcf_df (pd.DataFrame): The input DataFrame containing the mutations.

        Returns:
            pd.DataFrame: A filtered DataFrame containing the mutations.
        """

        self.log.attempt('Filtering varients to only include those likely to arise through EMS exposure...')
        ems_snps = [('G', 'A'), ('C', 'T'), ('A', 'G'), ('T', 'C')]
        
        # Filter
        vcf_df = vcf_df[vcf_df[['ref', 'alt']].apply(tuple, axis=1).isin(ems_snps)]
        self.log.success('Varients filtered.')
        
        return vcf_df


    def drop_genos_with_negative_ratios(self, vcf_df: pd.DataFrame)-> pd.DataFrame:
        '''
        Removes those genotypes that give rise to negative delta SNP ratios.
        args:
            vcf_df: pd.DataFrame - VCF dataframe
        
        Returns:
            vcf_df: pd.DataFrame - Filtered dataframe with no negative delta-snp
            values
        '''

        self.log.attempt('Trying to remove Genotypes that produce negative delta SNP ratios')
        try: 
            vcf_df = vcf_df[vcf_df['ratio'] >= 0]
            self.log.success('Genotypes that produce negative delta SNP ratios removed.')
            
            return vcf_df

        except Exception as e:
            self.log.fail(f'There was an error removing genotypes that produce nagative delta snp ratios:{e}')


    def mask_known_snps(self, snpmask_df: pd.DataFrame, vcf_df: pd.DataFrame) -> pd.DataFrame:
        '''
        This fuction applys the SNP mask. If the user has provided a collection
        of background snps in a suitable format, (headers aren't case sensitive
        but they must exists between the two VCFs) those background snps will
        be removed before analysis proceeds. This leads a much cleaner output, 
        and is particularly useful if aligning EMS mutants in an background that
        diverges from the reference genome, as there will be many spurious 
        background variants that are mostly irrelevant to your analysis. 

        args: 
            snpmask_df: pd.DataFrame - df containing background snps
            vcf_df: pd.DataFrame - VCF dataframe
        returns:
            vcf_df: pd.DataFrame - Filtered dataframe without background snps
        '''
        
        # Convert column names to lower case
        snpmask_df.columns = snpmask_df.columns.str.lower()
        vcf_df.columns = vcf_df.columns.str.lower()

        print("snpmask_df headers:", snpmask_df.columns.tolist())
        print("vcf_df headers:", vcf_df.columns.tolist())

        # Create a set from the 'chrom', 'pos', 'ref', 'alt' columns
        known_snps_set = set(zip(snpmask_df['chrom'], snpmask_df['pos'], 
                                  snpmask_df['ref'], snpmask_df['alt']))

        # Filter the vcf_df to only include rows not in the known_snps_set
        return vcf_df[~vcf_df[['chrom', 'pos', 'ref', 'alt']]
                              .apply(tuple, axis=1).isin(known_snps_set)]


class FeatureProduction:
    def __init__(self, logger, name):
        self.log = logger
        
        self.name = name

    @staticmethod
    def _delta_snp_array(wtr: np.ndarray, wta:np.ndarray, mur: np.ndarray, mua: np.ndarray)-> np.ndarray:
        """
        Calculates delta SNP feature, which quantifies divergence in 
            read depths between the two bulks.

        Args: 
            wtr, wta, mur, mua (numpy array)
            Read depths of wt reference and alt reads
            and the read depths of mutant reference and alt reads 

        Returns: Delta-snp calculation, which is a quantification of 
            allelic segregation at each polymorphic site.
        """ 

        return ((wtr) / (wtr + wta)) - ((mur) / (mur + mua))

    @staticmethod
    def _g_statistic_array(wtr: np.ndarray, wta: np.ndarray, mur: np.ndarray, mua: np.ndarray)->np.ndarray:
        """
        Calculates g-statistic feature, which is a more statistically driven 
        approach to calculating read-depth divergence from expected values. 
        Chi square ish.
        """ 

        np.seterr(all='ignore')
        zero_mask = wtr + mur + wta + mua != 0
        denominator = wtr + mur + wta + mua

        e1 = np.where(zero_mask, (wtr + mur) * (wtr + wta) / (denominator), 0)
        e2 = np.where(zero_mask, (wtr + mur) * (mur + mua) / (denominator), 0)
        e3 = np.where(zero_mask, (wta + mua) * (wtr + wta) / (denominator), 0)
        e4 = np.where(zero_mask, (wta + mua) * (mur + mua) / (denominator), 0)

        llr1 = np.where(wtr / e1 > 0, 2 * wtr * np.log(wtr / e1), 0.0)
        llr2 = np.where(mur / e2 > 0, 2 * mur * np.log(mur / e2), 0.0)
        llr3 = np.where(wta / e3 > 0, 2 * wta * np.log(wta / e3), 0.0)
        llr4 = np.where(mua / e4 > 0, 2 * mua * np.log(mua / e4), 0.0)

        return np.where(e1 * e2 * e3 * e4 == 0, 0.0, llr1 + llr2 + llr3 + llr4)
   

    def calculate_delta_snp_and_g_statistic(self, vcf_df: pd.DataFrame)-> pd.DataFrame:
        """
        Calculate delta SNP ratio and G-statistic
        
        Args: 
        vcf_df 
        pd.DataFrame VCF with no NA and indels.
        
        Returns: 
        pd.DataFrame VCF with delta-snp and g-stat calculated
        """
        self.log.attempt(f"Initialize calculations for delta-SNP ratios and G-statistics")
        try:
            suppress = False
            # calculate delta snps
            
            wt_ref = vcf_df['wt_ref'].values
            wt_alt = vcf_df['wt_alt'].values
            mu_ref = vcf_df['mu_ref'].values
            mu_alt = vcf_df['mu_alt'].values
            
            vcf_df['ratio'] = FeatureProduction._delta_snp_array(
                wt_ref, wt_alt, mu_ref, mu_alt
            )

            vcf_df['G_S'] = FeatureProduction._g_statistic_array(
                   wt_ref, wt_alt, mu_ref, mu_alt
            )

            vcf_df['RS_G'] = vcf_df['ratio'].values * vcf_df['G_S'].values
            self.log.success("Calculation of delta-SNP ratios and G-statistics was successful.")
            
            return vcf_df
        
        except Exception as e:
            self.log.fail(f"An error occurred during calculation: {e}")


    def _fit_chr_facets(self, vcf_df:pd.DataFrame, smoothing_function, loess_span: float, smooth_edges_bounds: int)->pd.DataFrame:
        """
        Internal Function for fitting smoothing model to chromosome facets. 
        Uses function "fit_single_chr" to interate over chromosomes as facets
        to generate LOESS smoothed values for g-statistics and delta-SNP feature
        
        Input: vcf_df
        
        output: vcf_df updated with gs, ratio, and gs-ratio yhat values
        """        
        
        df_list = []
        chr_facets = vcf_df["chrom"].unique()
        
        
        def _fit_single_chr(df_chr, chr, smoothing_function, loess_span, smooth_edges_bounds):
            """
            Input: df_chr chunk, extends the data 15 data values in each
            direction (to mitigate LOESS edge bias), fits smoothed values and 
            subsequently removes the extended data. 
            Returns: df with fitted values included.
            """
        
            self.log.attempt(f"LOESS of chr:{chr} for {self.name}...")
            try:
                positions = df_chr['pos'].to_numpy()
                
                self.log.note('Creating mirrored data on chr ends...')
                deltas = ([pos - positions[i - 1]
                        if i > 0 else pos for i, pos in enumerate(positions)]
                        )
                deltas_pos_inv = deltas[::-1][-smooth_edges_bounds:-1]
                deltas_neg_inv = deltas[::-1][1:smooth_edges_bounds]
                deltas_mirrored_ends = deltas_pos_inv + deltas + deltas_neg_inv

                psuedo_pos = []
                for i, pos in enumerate(deltas_mirrored_ends):
                    if i == 0:
                        psuedo_pos.append(0)
                    
                    if i > 0:
                        psuedo_pos.append(pos + psuedo_pos[i - 1])

                df_chr_inv_neg = df_chr[::-1].iloc[-smooth_edges_bounds:-1]
                df_chr_inv_pos = df_chr[::-1].iloc[1:smooth_edges_bounds]
                df_chr_smooth_list = [df_chr_inv_neg, df_chr, df_chr_inv_pos]
                df_chr = pd.concat(df_chr_smooth_list, ignore_index=False)

                df_chr['pseudo_pos'] = psuedo_pos
                X = df_chr['pseudo_pos'].values
                
                self.log.note("Fitting delta snp ratios....")
                ratio_Y = df_chr['ratio'].values
                df_chr['ratio_yhat'] = (
                    smoothing_function(ratio_Y, X, frac=loess_span)[:, 1]
                )
                
                self.log.note("Fitting G-statistic values...")
                G_S_Y = df_chr['G_S'].values
                df_chr['GS_yhat'] = (
                    smoothing_function(G_S_Y, X, frac=loess_span)[:, 1]
                )

                self.log.note("Fitting ratio-scaled G values....")
                RS_G_Y = df_chr['RS_G'].values
                df_chr['RS_G_yhat'] = (
                    smoothing_function(RS_G_Y, X, frac=loess_span)[:, 1]
                )

                df_chr = df_chr[smooth_edges_bounds:-smooth_edges_bounds].drop(
                    columns='pseudo_pos'
                )

                self.log.success(f"LOESS of chr:{chr} for {self.name} complete")
            
                return df_chr

            except Exception as e:
                self.log.fail(f"There was an error while smoothing chr:{chr} for {self.name}:{e}")

            return None

        self.log.attempt('Smoothing chromosome facets')
        try:
            for i in chr_facets:
                df_chr = vcf_df[vcf_df['chrom'] == i]
                result = _fit_single_chr(df_chr, i, smoothing_function, loess_span, smooth_edges_bounds)
        
                if result is not None:
                    df_list.append(result)
            
            self.log.success('Chromosome facets LOESS smoothed')
            
            return pd.concat(df_list)
        
        except Exception as e:
            self.log.fail(f'There was an error during LOESS smoothing of chromosome facets:{e}')

            return None
    
    
    def fit_model(self, vcf_df: pd.DataFrame, smoothing_function, loess_span: float, smooth_edges_bounds: int)->pd.DataFrame:
        """
        LOESS smoothing of ratio and G-stat by chromosome
        
        Input: Cleaned dataframe with delta SNPs and G-stats calculated
        
        Returns: Dataframe containing LOESS fitted values for ratio, g-stat and 
        ratio-scaled g-stat
        """

        self.log.attempt("Initialize LOESS smoothing calculations.")
        self.log.attempt(f"span: {loess_span}, Edge bias correction: {smooth_edges_bounds}") 
        try:

            vcf_df = self._fit_chr_facets(vcf_df, smoothing_function, loess_span, smooth_edges_bounds)
        
            self.log.success("LOESS smoothing calculations successful.")    
            
            return vcf_df
        
        except Exception as e:
            self.log.fail( f"An error occurred during LOESS smoothing: {e}")
    

    @staticmethod
    def _empirical_cutoff(vcf_df_position: pd.DataFrame, vcf_df_wt: pd.DataFrame, vcf_df_mu: pd.DataFrame, frac: float, smoothing_function, loess_span: float):
        """
        randomizes the input read_depths, breaking the position/feature link.
        this allows the generation of a large dataset which has no linkage 
        information, establishing an empirical distribution of potenial 
        delta-snps and g-statistics. Given the data provided, we can then set 
        reasonable cutoff values for the fitted values
        
        There is probably a less computationally intensive statistical framework 
        for doing this, especially for the g-statistics....
        
        input: various arrays pulled from vcf_df. 
            vcf_df_position(array) - genome positions
            vcf_df_wt(array) - wt read depth
            vcf_df_mu(array) - mu read depth

        """

        dfShPos = vcf_df_position.sample(frac=frac)
        dfShwt = vcf_df_wt.sample(frac=frac)
        dfShmu = vcf_df_mu.sample(frac=frac)

        smPos = dfShPos['pos'].to_numpy()
        sm_wt_ref = dfShwt['wt_ref'].to_numpy()
        sm_wt_alt = dfShwt['wt_alt'].to_numpy()
        sm_mu_ref = dfShmu['mu_ref'].to_numpy()
        sm_mu_alt = dfShmu['mu_alt'].to_numpy()

        smGstat = FeatureProduction._g_statistic_array(
            sm_wt_ref, sm_wt_alt, sm_mu_ref, sm_mu_alt
        )
        smRatio = FeatureProduction._delta_snp_array(
            sm_wt_ref, sm_wt_alt, sm_mu_ref, sm_mu_alt
        )

        smRS_G = smRatio * smGstat
        smRS_G_y = smoothing_function(smRS_G, smPos, frac=loess_span)[:, 1]

        return smGstat, smRatio, smRS_G, smRS_G_y


    def calculate_empirical_cutoffs(self, vcf_df: pd.DataFrame, smoothing_function, loess_span: float, shuffle_iterations: int)->tuple:
        self.log.attempt('Bootstrapping to generate empirical cutoffs...')
        try:
            vcf_df_position = vcf_df[['pos']].copy()
            vcf_df_wt = vcf_df[['wt_ref', 'wt_alt']].copy()
            vcf_df_mu = vcf_df[['mu_ref', 'mu_alt']].copy()

            # Calculate the fraction to sample
            # Keep subsampling from getting too wild with high variant #s
            n = len(vcf_df_position)
            if n > 20000:
                frac = 0.05
            
            elif n < 1000:
                frac = 1
            
            else:
                # Interpolation to scale subsampling from len(vcf_df_position)
                frac = np.interp(n, [1000, 20000], [1, 0.05])
            
            bootstrap_perc = frac*100
            self.log.note(f"{n} Variants left after filtering.") 
            self.log.note(f"Bootstrapping process will subsample {bootstrap_perc}% of data {shuffle_iterations} times")
            
            # Define the arguments for each process
            args = [(vcf_df_position, vcf_df_wt, vcf_df_mu, frac, smoothing_function, 
            loess_span    
                ) 
                for _ in range(shuffle_iterations)
            ]
            
            # Create a pool of processes
            with Pool() as pool:
                results = pool.starmap(FeatureProduction._empirical_cutoff, args)

                sm_g_stat_lst = [result[0] for result in results if result is not None]
                sm_ratio_lst = [result[1] for result in results if result is not None]
                sm_ratio_scaled_g_lst = [result[2] for result in results if result is not None]
                sm_ratio_scaled_g_y_lst = [result[3] for result in results if result is not None]
            
            # Calculate the cutoffs from the results
            gs_cutoff = np.percentile(sm_g_stat_lst, 95)
            rs_cutoff = np.percentile(sm_ratio_lst, 95)
            rsg_cutoff = np.percentile(sm_ratio_scaled_g_lst, 95)
            rsg_y_cutoff = np.percentile(sm_ratio_scaled_g_y_lst, 99.99)

            self.log.success('Bootstrapping complete!')
            self.log.note(f"G-statistic cutoff = {gs_cutoff}.")
            self.log.note(f"Ratio-scaled G-statistic cutoff = {rsg_cutoff}.")
            self.log.note(f"LOESS smoothed Ratio-scaled G-statistic cutoff = {rsg_y_cutoff}.")

            return gs_cutoff, rs_cutoff, rsg_cutoff, rsg_y_cutoff

        except Exception as e:
            self.log.fail(f'Bootstrapping to generate empirical cutoffs failed:{e}')

            return None, None, None


    def label_df_with_cutoffs(self, vcf_df: pd.DataFrame, gs_cutoff: float, rsg_cutoff: float, rsg_y_cutoff:float)->pd.DataFrame:
        
        try:
            vcf_df['G_S_05p'] = [1 if (np.isclose(x, gs_cutoff) 
                or (x > gs_cutoff)) else 0 for x in vcf_df['G_S']
            ]
            vcf_df['RS_G_05p'] = [1 if (np.isclose(x, rsg_cutoff) 
                or (x > rsg_cutoff)) else 0 for x in vcf_df['RS_G']
            ]
            vcf_df['RS_G_yhat_01p'] = [1 if (np.isclose(x, rsg_y_cutoff) 
                or (x > rsg_y_cutoff)) else 0 for x in vcf_df['RS_G_yhat']
            ]
        
            return vcf_df
        
        except Exception as e:
            self.log.fail(f"An error while labeling dataframe with cutoffs: {e}")

            return None


class TableAndPlots:
    
    def __init__(self, logger, name, vcf_df, analysis_out_prefix):
        self.log = logger
        self.name = name
        self.vcf_df = vcf_df
        self.analysis_out_prefix = analysis_out_prefix


    def _identify_likely_candidates(self):
        try:
            return self.vcf_df[
                (self.vcf_df['RS_G_yhat_01p'] == 1) |
                (self.vcf_df['G_S_05p'] == 1) |
                (self.vcf_df['RS_G_05p'] == 1)
            ]
        except KeyError as e:
            self.log.fail(f"Column {e} not found in DataFrame. Please ensure column names are correct.")


    def _sort_likely_candidates(self, df):
        """
        Sorts the DataFrame based on 'RS_G_yhat', 'G_S_05', 'RS_G_05', 
        with 'GS_G_yhat' being the top priority.
        """
        try:
            sorted_df = df.sort_values(by=['RS_G_yhat_01p', 'G_S_05p', 'RS_G_05p'], ascending=False)

            return sorted_df

        except KeyError as e:
            self.log.fail(f"Column {e} not found in DataFrame. Please ensure column names are correct.")


    def _save_candidates(self, df):
        """
        Saves the DataFrame of likely candidates to a CSV file.
        """
        try:
            output_file = f"{self.analysis_out_prefix}_likely_candidates.csv"
            df.to_csv(output_file, index=False)
        
            self.log.success(f"Saved likely candidates to {output_file}")
        
        except Exception as e:
            self.log.fail(f"Failed to save likely candidates: {e}")
        

    def sort_save_likely_candidates(self):
        vcf_df_likely_cands = self._identify_likely_candidates()
        likely_cands_sorted = self._sort_likely_candidates(vcf_df_likely_cands)
        self._save_candidates(likely_cands_sorted)


    def generate_plots(self, gs_cutoff: float, rsg_cutoff: float, rsg_y_cutoff: float):
        plot_scenarios = [
            ('G_S', 'G-statistic', 'G-statistic', gs_cutoff, False),
            ('GS_yhat', 'Lowess smoothed G-statistic', 'Fitted G-statistic', None, True),
            ('RS_G', 'Ratio-scaled G statistic', 'Ratio-scaled G-statistic', rsg_cutoff, False),
            ('ratio', 'Delta SNP ratio', 'Ratio', None, False),
            ('ratio_yhat', 'Fitted Delta SNP ratio', 'Fitted delta SNP ratio', None, True),
            ('RS_G_yhat', 'Lowess smoothed ratio-scaled G statistic', 'Fitted Ratio-scaled G-statistic', rsg_y_cutoff, True)
        ]

        for plot_scenario in plot_scenarios:
            plot_created = self._create_plot(*plot_scenario)

            if plot_created is not None:
                self._save_plot(plot_created, *plot_scenario)


    def _create_plot(self, y_column, title_text, ylab_text, cutoff_value=None, lines=False):
        try:
            mb_conversion_constant = 0.000001
            self.vcf_df['pos_mb'] = self.vcf_df['pos'] * mb_conversion_constant
            chart = ggplot(self.vcf_df, aes('pos_mb', y=y_column))
            title = ggtitle(title_text)
            axis_x = xlab("Position (Mb)")
            axis_y = ylab(ylab_text)

            if cutoff_value is not None:
                cutoff = geom_hline(yintercept=cutoff_value, color='red',
                                    linetype="dashed", size=0.3)

                plot = (chart
                        + geom_point(color='goldenrod', size=0.8)
                        + theme_linedraw()
                        + facet_grid('. ~ chrom', space='free_x', scales='free_x')
                        + title
                        + axis_x
                        + axis_y
                        + theme(panel_spacing=0.025)
                        + cutoff
                )

            else:
                plot = (chart
                        + geom_point(color='goldenrod', size=0.8)
                        + theme_linedraw()
                        + facet_grid('. ~ chrom', space='free_x', scales='free_x')
                        + title
                        + axis_x
                        + axis_y
                        + theme(panel_spacing=0.025)
                )

            if lines:
                plot += geom_line(color='blue')

            return plot

        except Exception as e:
            self.log.fail(f"Plot creation failed for {self.name}, column {y_column}: {e}")
            return None


    def _save_plot(self, plot, y_column, *args):
        try:
            plot_path = f"{self.analysis_out_prefix}_{y_column.lower()}.png"
            plot.save(filename=plot_path, height=6, width=8, units='in', dpi=500)
            self.log.success(f"Plot saved {plot_path}")
            return True

        except Exception as e:
            self.log.fail(f"Saving plot failed for {self.name}, column {y_column}: {e}")
            return False
