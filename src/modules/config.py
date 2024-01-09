#!/usr/bin/env python
import os
import inspect
from datetime import datetime
import logging
import utilities_ulid as ulid

BASE_DIR = os.getcwd()
SRC_DIR =  os.path.join(BASE_DIR, 'src')
REFERENCE_DIR = os.path.join(BASE_DIR, 'references')
INPUT_DIR = os.path.join(BASE_DIR, 'input')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
LOG_DIR = os.path.join(SRC_DIR, 'logs')
TEMPLATE_DIR = os.path.join(SRC_DIR,'templates')
STATIC_DIR = os.path.join(SRC_DIR,'static')
MODULES_DIR = os.path.join(SRC_DIR,'modules')

import logging
import sqlite3

class LogHandler:
    def __init__(self, name, db_name="thale_bsa_sqldb.db"):
        # Initialize log file parameters
        self.name = name 
        self.init_timestamp = datetime.now().strftime("%Y.%m.%d-%H.%M")
        self.ulid = ulid.ulid()
        
        # Construct log filename and spin up logger
        self.log_filename = f'{self.init_timestamp}_{self.name}_{self.ulid}.log'
        self.log_path = os.path.join(LOG_DIR, self.log_filename)
        self.logger = self.setup_logger()

        # Initialize log database for storing log parameters between runs
        self.db_name = db_name
        self.conn = sqlite3.connect(self.db_name)
        self._create_tables()

    def setup_logger(self):
        '''Initialize logger, which is designed to be passed to class instances 
        so that logging can be passed around to new functionalities as the program expands.'''
        logger = logging.getLogger(self.name)
        logger.setLevel(logging.INFO)  # Set the default level for the logger

        # Create a FileHandler and set the level to INFO
        file_handler = logging.FileHandler(self.log_path)
        file_handler.setLevel(logging.INFO)

        # Create a formatter and add it to the handler
        formatter = logging.Formatter('%(message)s')
        file_handler.setFormatter(formatter)

        # Add the handler to the logger
        logger.addHandler(file_handler)

        return logger

    def _obtain_execution_frames(self):
        '''obtains the module and the function in which the logger is called. 
        making debugging easier (maybe)'''
        current_frame = inspect.currentframe()
        caller_frame = inspect.getouterframes(current_frame)[2]
        
        # Get function name and script name from one level higher
        function_name = caller_frame[0].f_code.co_name
        script_name = os.path.basename(caller_frame[1])
        
        return script_name, function_name

    def _construct_message(self, prefix, script_name, function_name, message_in):
        log_handler_timestamp = datetime.now().strftime("%Y.%m.%d ~%H:%M")
        if function_name != '<module>':
            message_out = f"{log_handler_timestamp} {prefix} ({script_name}>{function_name}) {message_in}"
        else:
            message_out = f"{log_handler_timestamp} {prefix} ({script_name}> module) {message_in}"
        return message_out 

    def trigger(self, message):
        script_name, function_name = self._obtain_execution_frames()
        log_message = self._construct_message(
            '[Flask Trigger]', script_name, function_name, message
        )
        self.logger.info(log_message)
        print(log_message)

    def attempt(self, message):
        script_name, function_name = self._obtain_execution_frames()
        log_message = self._construct_message(
            '[Attempt]', script_name, function_name, message
        )
        self.logger.info(log_message)
        print(log_message)

    def success(self, message):
        script_name, function_name = self._obtain_execution_frames()
        log_message = self._construct_message(
            '[Success]', script_name, function_name, message
        )
        self.logger.info(log_message)
        print(log_message)

    def note(self, message):
        script_name, function_name = self._obtain_execution_frames()
        log_message = self._construct_message(
            '[Note]', script_name, function_name, message
        )
        self.logger.info(log_message)
        print(log_message)

    def fail(self, message):
        script_name, function_name = self._obtain_execution_frames()
        log_message = self._construct_message(
            '[!-FAIL-!]', script_name, function_name, message
        )
        self.logger.info(log_message)
        print(log_message)
        quit()

    def warning(self, message):
        script_name, function_name = self._obtain_execution_frames()
        log_message = self._construct_message(
            '[!-WARNING-!]', script_name, function_name, message
        )
        self.logger.info(log_message)
        print(log_message)

    def bash(self, message):
        script_name, function_name = self._obtain_execution_frames()
        log_message = self._construct_message(
            '[sh]', script_name, function_name, message
        )
        self.logger.info(log_message)
        print(log_message)

    def delimiter(self, message):
        delimiter_timestamp = datetime.now().strftime("%Y.%m.%d ~%H:%M")
        log_message = (f'''
>=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-<
-. .-.   .-. .-.   .-. .-.   .-. .-.   .-. .-.   .-. .-.   .-. .-.   .-. .-.   .
||\|||\ /|||\|||\ /|||\|||\ /|||\|||\ /|||\|||\ /|||\|||\ /||\|||\ /|||\|||\ /||
|/ \|||\|||/ \|||\|||/ \|||\|||/ \|||\|||/ \|||\|||/ \|||\||/ \|||\|||/ \|||\|||
~   `-~ `-`   `-~ `-`   `-~ `-~   `-~ `-`   `-~ `-`   `-~ `~   `-~ `-`   `-~ `-`
>=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-<
{delimiter_timestamp} {message}
>=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-<
            ''')
        self.logger.info(log_message)
        print(log_message)    

    # Log database functions
    def _create_tables(self):
        create_core = '''
            CREATE TABLE IF NOT EXISTS core (
                core_ulid TEXT,
                core_log_path TEXT,
                core_timestamp TEXT,
                PRIMARY KEY(core_ulid)
            )
        '''

        create_vcf = '''
            CREATE TABLE IF NOT EXISTS vcf (
                vcf_ulid TEXT,
                line_name TEXT,
                core_ulid TEXT,
                vcf_log_path TEXT,
                vcf_timestamp TEXT,
                PRIMARY KEY (vcf_ulid, line_name)
            )
        '''

        create_analysis = '''
            CREATE TABLE IF NOT EXISTS analysis (
                analysis_ulid TEXT,
                line_name TEXT,
                core_ulid TEXT,
                vcf_ulid TEXT,
                analysis_log_path TEXT,
                analysis_timestamp TEXT,
                PRIMARY KEY (analysis_ulid, line_name)
            )
        '''

        try:
            self.conn.execute(create_core)
            self.conn.execute(create_vcf)
            self.conn.execute(create_analysis)
            self.conn.commit()
        
        except Exception as e:
            print(f'There was an error during table creation: {e}')


    def add_db_record(self, current_line_name=None, core_ulid=None, vcf_ulid=None):

        try:
            if self.name == 'core':
                add = '''
                    INSERT INTO core (core_ulid, core_log_path, core_timestamp)
                    VALUES(?, ?, ?)
                '''
                values = (self.ulid, self.log_path, self.init_timestamp)
            
            elif self.name == f'vcf_{current_line_name}':
                add = '''
                    INSERT INTO vcf (vcf_ulid, line_name, core_ulid, vcf_log_path, vcf_timestamp)
                    VALUES (?, ?, ?, ?, ?)
                '''
                values = (self.ulid, current_line_name, core_ulid, self.log_path, self.init_timestamp)
            
            elif self.name == f'analysis_{current_line_name}':
                add = '''
                    INSERT INTO analysis (analysis_ulid, line_name, core_ulid, vcf_ulid, analysis_log_path, analysis_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                '''
                values = (self.ulid, current_line_name, core_ulid, vcf_ulid, self.log_path, self.init_timestamp)
            
            else:
                raise ValueError("Invalid table name")

            self.conn.execute(add, values)
            self.conn.commit()
        
        except Exception as e:
            print(f'There was an error adding entries to database:{e}')
