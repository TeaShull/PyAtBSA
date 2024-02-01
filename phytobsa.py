#!/usr/bin/env python
# import utililites & parse arguments
from modules.utilities_parser import ArgumentParser
arg_parser = ArgumentParser() #First so DATA_DIR can be set & populate paths
args = arg_parser.args
from modules.utilities_logging import LogHandler
from modules.utilities_general import LogDbUtilites

# import core modules
from modules.core_vcf_gen import VCFGenerator
from modules.core_bsa import BSA
from modules.core_variables import (
    Lines, AutomaticLineVariableDetector, VCFGenVariables, BSAVariables
) # Paths are organized in settings.paths module and used mostly here 

def setup_vcf_variables(core_log, line, args):
    '''
    Setup variables needed to run the VCF generator. Args are either passed by 
    user or sourced from config.ini (check phytobsa settings -h to set defaults. 
    One can also directly edit config.ini in a text editor)
    '''
    vcf_vars = VCFGenVariables(
        core_log, 
        lines=line,
        reference_genome_path=args.reference_genome_path, 
        reference_genome_source=args.reference_genome_source,
        omit_chrs_patterns=args.omit_chrs_patterns,
        snpEff_species_db=args.snpEff_species_db,
        threads_limit=args.threads_limit, 
        call_variants_in_parallel=args.call_variants_in_parallel,
        cleanup=args.cleanup,
        cleanup_filetypes=args.cleanup_filetypes
    )
    return vcf_vars

def setup_bsa_variables(core_log, line, args):
    '''
    Setup variables needed to run BSA. Args are either passed by user or sourced
    from config.ini (check phytobsa settings -h to set defaults. One can also
    directly edit config.ini in a text editor)
    '''
    bsa_vars = BSAVariables(
        core_log, 
        lines=line, 
        loess_span=args.loess_span, 
        smooth_edges_bounds=args.smooth_edges_bounds, 
        shuffle_iterations=args.shuffle_iterations,
        filter_indels=args.filter_indels,
        filter_ems=args.filter_ems,
        snpmask_path=args.snpmask_path
    )
    return bsa_vars

def main():
    '''
    Classes are passed a LogHandler [modules.utilities_logging] instance on init.
    Unique IDs (ulid) are assigned upon init of LogHandler. 
    
    ulids link file outputs to their log. 
    ulid's are stored in LogHandler instances, and exported to the Lines data
    class during vcf_gen and BSA(analysis). 

    LogHandler manages the sqlite log database to save run information. 

    log list:
    'core' - logs all main program logic in thale_bsa.py, before child log init.  
        'vcf_gen' - log pertaining to parent_functions.vcf_generation
        'analysis' - log peratining to parent_functions.bsa_analysis 
    '''
    
    if args.command != 'logdb':
        core_log = LogHandler('core')
        core_log.note(f'Core log begin. ulid: {core_log.ulid}')
        core_log.add_db_record()
    
    # Determine which routine to run
    if args.command == 'analysis':
        #init Lines var class
        line = Lines(core_log, args.name) # more info in modules.core_variables

        #Parse user input into Lines Class. Input checks are done here
        line.usr_in_line_variables(args.vcf_table_path, arg.segregation_type)
        
        # Process line & args, prep variables needed for runtime + more checks
        bsa_vars = setup_bsa_variables(core_log, line, args)
        
        # pass bsa_vars Class instance to the BSA pipeline, and run
        bsa = BSA(core_log, bsa_vars)    
        bsa.run_pipeline()
    
    elif args.command == 'vcf_generator':
        
        #init Lines var class
        line = Lines(core_log, args.name) 
        
        #Parse user input into Lines Class. Input checks are done here
        line.usr_in_line_variables(args.reference_genome_path, 
            args.wt_input, args.mu_input
        ) 

        # [modules.core_variables]
        #process line & args, prep variables needed for runtime + more checks
        vcf_vars = setup_vcf_variables(core_log, line, args)
        
        #Pass vcf_vars Class instance to the VCF generater and run
        vcf_gen = VCFGenerator(core_log, vcf_vars)
        vcf_gen.run_subprocess()

    elif args.automatic:
        #automatically generate Lines from files in Input, if formatted properly
        auto_vars = AutomaticLineVariableDetector(core_log)
        auto_vars.automatic_line_variables()

        #process line & args, prep variables needed for runtime + more checks
        vcf_vars = setup_vcf_variables(core_log, auto_vars.lines, args)
        
        #Pass vcf_vars Class instance to the VCF generater & run
        vcf_gen = VCFGenerator(core_log, vcf_vars)
        vcf_gen.run_subprocess()
        
        # Pass "Lines" used in vcf_gen instance to bsa_vars.
        bsa_vars = setup_bsa_variables(core_log, vcf_gen.lines, args)
        
        #Pass bsa_vars containing lines from vcf_gen step & run BSA pipeline
        bsa = BSA(core_log, bsa_vars)
        bsa.run_pipeline()

    elif args.command == 'logdb':
        logdb_utils = LogDbUtilites()
        if args.vcf_ulid_log:
            logdb_utils.print_vcf_log_data(args.vcf_ulid_log)
        if args.analysis_ulid_log:
            logdb_utils.print_analysis_log_data(args.analysis_ulid_log)
        if args.line_name_log:
            logdb_utils.print_line_name_data(args.line_name_log)
        if args.core_ulid_log:
            logdb_utils.print_core_ulid_data(args.core_ulid_log)

if __name__ == "__main__":
    main()
