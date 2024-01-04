import os
import numpy as np
import pandas as pd
import statsmodels.api as sm
import warnings
from plotnine import (
    ggplot, aes, geom_point, geom_line, theme_linedraw,
    facet_grid, theme, ggtitle, xlab, ylab, geom_hline
)
from config import (
    LogHandler, BASE_DIR, SRC_DIR,
    INPUT_DIR, MODULES_DIR, OUTPUT_DIR
)

class AnalysisUtilities:
    
    def __init__(self, current_line_name, vcf_ulid, logger):
        self.current_line_name = current_line_name
        self.log = logger
        self.vcf_ulid = vcf_ulid
        self.analysis_out_prefix = f'{self.log.ulid}_-{current_line_name}_analysis'

        if self.vcf_ulid:
            self.analysis_out_path = os.path.join(
                OUTPUT_DIR, 
                f'{self.vcf_ulid}_-{current_line_name}', 
                {self.analysis_out_prefix}
            )
        else:
            self.analysis_out_path = os.path.join(
                OUTPUT_DIR,
                {self.analysis_out_prefix}
            )

    def drop_na_and_indels(self, vcf_df):
        """Drops NA and insertion/deletions from VCF dataframe.
        Input: VCF dataframe
        Output: Cleaned VCF dataframe with no NA or indels"""
        self.log.attempt('Removing NAs and indels')
        
        try:
            # Use .loc for assignment to avoid the warning
            apply_args = (lambda x: x if len(x) == 1 else np.nan)
            vcf_df.loc[:, "ref"] = vcf_df["ref"].apply(apply_args)
            vcf_df.loc[:, "alt"] = vcf_df["alt"].apply(apply_args)
            vcf_df.dropna(axis=0, how='any', subset=["ratio"], inplace=True)
            self.log.success(f'Indels dropped, and NaN values for {self.current_line_name} cleaned successfully.')
            
            return vcf_df
        
        except Exception as e:
            self.log.fail(f"An error occurred during data processing: {e}")
            
            return None

    def calculate_delta_snp_and_g_statistic(self, vcf_df):
        """Calculate delta SNP ratio and G-statistic
        Input: Cleaned VCF dataframe with no NA and indels.
        Returns: VCF dataframe with delta-snp and g-stat calculated"""
        self.log.attempt(f"Initialize calculations for delta-SNP ratios and G-statistics")
        try:
            suppress = False
            vcf_df['ratio'] = self._delta_snp_array(
                vcf_df['wt_ref'], vcf_df['wt_alt'], 
                vcf_df['mu_ref'], vcf_df['mu_alt'],
                suppress
            )
            vcf_df['G_S'] = self._g_statistic_array(
                vcf_df['wt_ref'], vcf_df['wt_alt'], 
                vcf_df['mu_ref'], vcf_df['mu_alt'],
                suppress
            )

            vcf_df['RS_G'] = vcf_df['ratio'] * vcf_df['G_S']
            return vcf_df
            self.log.success("Calculation of delta-SNP ratios and G-statistics were successful.")
        except Exception as e:
            self.log.fail( f"An error occurred during calculation: {e}")

    def _delta_snp_array(self, wtr, wta, mur, mua, suppress):
        '''Calculates delta SNP feature, which quantifies divergence in read depths between the 
        two bulks.''' 
        if not suppress:
            self.log.attempt(f"Calculate delta-SNP ratios for {self.current_line_name}..."
            )
        try:
            result = ((wtr) / (wtr + wta)) - ((mur) / (mur + mua))
            if not suppress:
                self.log.success(f"Delta-SNP calculation successful for {self.current_line_name}"
                )
            return result
        except Exception as e:
            self.log.fail(
                f"Error in delta_snp_array for {self.current_line_name}: {e}"
            )
            return None

    def _g_statistic_array(self, o1, o3, o2, o4, suppress):
        '''Calculates g-statistic feature, which is a more statistically driven approach to 
        calculating read-depth divergence from expected values. Chi square ish.''' 
        if not suppress:
            self.log.attempt(f"Calculate G-statistics for {self.current_line_name}....")
        
        try:
            np.seterr(all='ignore')

            zero_mask = o1 + o2 + o3 + o4 != 0
            denominator = o1 + o2 + o3 + o4

            e1 = np.where(zero_mask, (o1 + o2) * (o1 + o3) / (denominator), 0)
            e2 = np.where(zero_mask, (o1 + o2) * (o2 + o4) / (denominator), 0)
            e3 = np.where(zero_mask, (o3 + o4) * (o1 + o3) / (denominator), 0)
            e4 = np.where(zero_mask, (o3 + o4) * (o2 + o4) / (denominator), 0)

            llr1 = np.where(o1 / e1 > 0, 2 * o1 * np.log(o1 / e1), 0.0)
            llr2 = np.where(o2 / e2 > 0, 2 * o2 * np.log(o2 / e2), 0.0)
            llr3 = np.where(o3 / e3 > 0, 2 * o3 * np.log(o3 / e3), 0.0)
            llr4 = np.where(o4 / e4 > 0, 2 * o4 * np.log(o4 / e4), 0.0)

            result = np.where(
                e1 * e2 * e3 * e4 == 0, 0.0, llr1 + llr2 + llr3 + llr4
            )
            if not suppress:
                self.log.success(f"G-statistic calculation complete for {self.current_line_name}"
                )
            return result

        except Exception as e:
            self.log.fail(f"Error in g_statistic_array for {self.current_line_name}: {e}")
            return None

    def loess_smoothing(self, vcf_df):
        """LOESS smoothing of ratio and G-stat by chromosome
        Input: Cleaned dataframe with delta SNPs and G-stats calculated
        Returns: Dataframe containing LOESS fitted values for ratio, g-stat and 
        ratio-scaled g-stat"""
        lowess_span = 0.3
        smooth_edges_bounds = 15
        self.log.attempt("Initialize LOESS smoothing calculations.")
        self.log.attempt("span: {lowess_span}, Edge bias correction: {smooth_edges_bounds}") 
        try:

            vcf_df = self._smooth_chr_facets(
                vcf_df, lowess_span, smooth_edges_bounds
            )
            self.log.success("LOESS smoothing calculations successful.")
            return vcf_df
        except Exception as e:
            self.log.fail( f"An error occurred during LOESS smoothing: {e}")
    
    def _smooth_chr_facets(self, df, lowess_span, smooth_edges_bounds):
        self.log.attempt('Smoothing chromosome facets')
        df_list = []

        chr_facets = df["chr"].unique()

        def smooth_single_chr(df_chr, chr):
            lowess_function = sm.nonparametric.lowess

            self.log.attempt(f"LOESS of chr:{chr} for {self.current_line_name}...")

            positions = df_chr['pos'].to_numpy()
            
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

            ratio_Y = df_chr['ratio'].values
            df_chr['ratio_yhat'] = (
                lowess_function(ratio_Y, X, frac=lowess_span)[:, 1]
            )

            G_S_Y = df_chr['G_S'].values
            df_chr['GS_yhat'] = (
                lowess_function(G_S_Y, X, frac=lowess_span)[:, 1]
            )

            df_chr['RS_G'] = df_chr['G_S'] * df_chr['ratio']
            RS_G_Y = df_chr['RS_G'].values
            df_chr['RS_G_yhat'] = (
                lowess_function(RS_G_Y, X, frac=lowess_span)[:, 1]
            )

            df_chr = df_chr[smooth_edges_bounds:-smooth_edges_bounds].drop(
                columns='pseudo_pos'
            )

            self.log.success(f"LOESS of chr:{chr} for {self.current_line_name} complete"
            )
            return df_chr

        for i in chr_facets:
            df_chr = df[df['chr'] == i]
            result = smooth_single_chr(df_chr, i)
            if result is not None:
                df_list.append(result)

        return pd.concat(df_list)

    def calculate_empirical_cutoffs(self, vcf_df):
        """Calculate empirical cutoffs.
        Input: processed VCF dataframe.  
        Returns: vcf_df, gs_cutoff, rsg_cutoff, rsg_y_cutoff as a tuple"""
        iterations = 1000
        lowess_span = 0.3
        self.log.attempt("Initialize calculation of empirical cutoffs")
        self.log.note(f"breaking geno/pheno association. iterations:{iterations}, LOESS span:{lowess_span}")
        
        try:
            vcf_df_position = vcf_df[['pos']].copy()
            vcf_df_wt = vcf_df[['wt_ref', 'wt_alt']].copy()
            vcf_df_mu = vcf_df[['mu_ref', 'mu_alt']].copy()

            gs_cutoff, rsg_cutoff, rsg_y_cutoff = self._empirical_cutoff(
                vcf_df_position, vcf_df_wt, vcf_df_mu, iterations, lowess_span
            )

            vcf_df['G_S_05p'] = [1 if (np.isclose(x, gs_cutoff) 
                or (x > gs_cutoff)) else 0 for x in vcf_df['G_S']
            ]
            vcf_df['RS_G_05p'] = [1 if (np.isclose(x, rsg_cutoff) 
                or (x > rsg_cutoff)) else 0 for x in vcf_df['RS_G']
            ]
            vcf_df['RS_G_yhat_01p'] = [1 if (np.isclose(x, rsg_y_cutoff) 
                or (x > rsg_y_cutoff)) else 0 for x in vcf_df['RS_G_yhat']
            ]
            return vcf_df, gs_cutoff, rsg_cutoff, rsg_y_cutoff

            self.log.success(f"G-statistic cutoff = {gs_cutoff}.")
            self.log.success(f"Ratio-scaled G-statistic cutoff = {rsg_cutoff}.")
            self.log.success(f"LOESS smoothed Ratio-scaled G-statistic cutoff = {rsg_y_cutoff}.")
            self.log.success(f"Empirical cutoff via randomization for {self.current_line_name} completed.")

        except Exception as e:
            self.log.fail(f"An error occurred during cutoff calculations: {e}")

    def _empirical_cutoff(self, vcf_df_position, vcf_df_wt, vcf_df_mu, shuffle_iterations, lowess_span):
        self.log.attempt(f"Calculate empirical cutoff for {self.current_line_name}...")
        
        try:
            lowess = sm.nonparametric.lowess
            smGstatAll, smRatioAll, RS_GAll, smRS_G_yhatAll = [], [], [], []
            suppress = True
            for _ in range(shuffle_iterations):
                dfShPos = vcf_df_position.sample(frac=1)
                dfShwt = vcf_df_wt.sample(frac=1)
                dfShmu = vcf_df_mu.sample(frac=1)

                smPos = dfShPos['pos'].to_numpy()
                sm_wt_ref = dfShwt['wt_ref'].to_numpy()
                sm_wt_alt = dfShwt['wt_alt'].to_numpy()
                sm_mu_ref = dfShmu['mu_ref'].to_numpy()
                sm_mu_alt = dfShmu['mu_alt'].to_numpy()

                smGstat = self.g_statistic_array(
                    sm_wt_ref, sm_wt_alt, sm_mu_ref, sm_mu_alt, suppress
                )
                smGstatAll.extend(smGstat)

                smRatio = self.delta_snp_array(
                    sm_wt_ref, sm_wt_alt, sm_mu_ref, sm_mu_alt, suppress
                )
                smRatioAll.extend(smRatio)

                smRS_G = smRatio * smGstat
                RS_GAll.extend(smRS_G)

                smRS_G_yhatAll.extend(lowess(
                    smRS_G, smPos, frac=lowess_span)[:, 1]
                )

            G_S_95p = np.percentile(smGstatAll, 95)
            RS_G_95p = np.percentile(RS_GAll, 95)
            RS_G_Y_99p = np.percentile(smRS_G_yhatAll, 99.99)

            result = G_S_95p, RS_G_95p, RS_G_Y_99p
            self.log.success(f"Empirical cutoff calculation completed for {self.current_line_name}")
            
            return result
        
        except Exception as e:
            self.log.fail(f"Error in empirical_cutoff for {self.current_line_name}: {e}")
            
            return None, None, None        

    def sort_save_likely_candidates(self, vcf_df):
        """Identify likely candidates"""
        self.log.attempt('Initialize the identification of likely candidates')
        self.log.note(f'associated VCF table ulid: {vcf_ulid}')

        try:
            # Identify likely candidates using G-stat and smoothed ratio-scaled G-stat
            vcf_df_likely_cands = vcf_df.loc[vcf_df['RS_G_yhat_01p'] == 1]
            likely_cands_sorted = vcf_df_likely_cands.sort_values(
                by=['G_S', 'RS_G_yhat'],
                ascending=[False, False],
                na_position='first'
            )

            # Save DataFrames to CSV files
            results_table_name =f"{self.analysis_out_prefix}_results_table.tsv"
            results_table_path = os.path.join(
                self.analysis_out_path, results_table_name
            )
            vcf_df.to_csv(results_table_path, sep='\t', index=False)

            candidates_table_name = f"{self.analysis_out_prefix}_candidates_table.tsv"
            candidates_table_path = os.path.join(
                self.analysis_out_path, candidates_table_name
            )
            likely_cands_sorted.to_csv(candidates_table_path, sep='\t', index=False)
            
            self.log.success(f"Results and candidates tables for {self.current_line_name} generated.")

        except Exception as e:
            self.log.fail(f"An error occurred during {self.current_line_name} table generation: {e}")

    def generate_plots(self, vcf_df, gs_cutoff, rsg_cutoff, rsg_y_cutoff):
        """Generate and save plots. Plot scenarios are below
        Plot scenarios format:
        ('y_column', 'title_text', 'ylab_text', cutoff_value=None, lines=False)"""
        plot_scenarios = [
            ('G_S', 'G-statistic', 'G-statistic', None, False),
            ('GS_yhat', 'Lowess smoothed G-statistic', 'Fitted G-statistic', 
                gs_cutoff, True
            ),
            ('RS_G', 'Ratio-scaled G statistic', 'Ratio-scaled G-statistic',
                rsg_cutoff, False
             ),
            ('ratio', 'Delta SNP ratio', 'Ratio', None, False),
            ('ratio_yhat', 'Fitted Delta SNP ratio', 'Fitted delta SNP ratio',
                None, True
             ),
            ('RS_G_yhat', 'Lowess smoothed ratio-scaled G statistic', 
                'Fitted Ratio-scaled G-statistic', rsg_y_cutoff, True
            ),
        ]

        self.log.attempt(f"Attempting to produce and save plots for {self.current_line_name}...")
        
        try:
            for plot_scenario in plot_scenarios:
                self.plot_data(vcf_df, *plot_scenario)
            
            self.log.delimiter(f"Results for {self.current_line_name} generated.")

        except Exception as e:
            self.log.fail(f"An error occurred while producing and saving plots: {e}")
        
    def plot_data(self, df, y_column, title_text, ylab_text, cutoff_value=None, lines=False):
        warnings.filterwarnings("ignore", module="plotnine\..*")
        self.log.attempt(f"Plot data and save plots for {self.current_line_name}...")

        try:
            mb_conversion_constant = 0.000001
            df['pos_mb'] = df['pos'] * mb_conversion_constant
            chart = ggplot(df, aes('pos_mb', y=y_column))
            title = ggtitle(title_text)
            axis_x = xlab("Position (Mb)")
            axis_y = ylab(ylab_text)

            if cutoff_value is not None:
                cutoff = geom_hline(yintercept=cutoff_value, color='red',
                                    linetype="dashed", size=0.3
                                    )
                plot = (chart
                        + geom_point(color='goldenrod', size=0.8)
                        + theme_linedraw()
                        + facet_grid('. ~ chr', space='free_x', scales='free_x')
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
                        + facet_grid('. ~ chr', space='free_x', scales='free_x')
                        + title
                        + axis_x
                        + axis_y
                        + theme(panel_spacing=0.025)
                        )

            if lines:
                plot += geom_line(color='blue')

            # Save plot
            OUTPUT_DIR = OUTPUT_DIR 
            plot_name = f"{self.analysis_out_prefix}_{y_column.lower()}.png"
            file_path_name = os.path.join(self.analysis_out_path, plot_name)
            plot.save(
                filename=file_path_name,
                height=6,
                width=8,
                units='in',
                dpi=500
            )

            self.log.success(f"Plot saved {file_path_name}")
        
        except Exception as e:
            self.log.fail(f"Plotting data failed for {self.current_line_name}: {e}")
