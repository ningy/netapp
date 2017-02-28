#!/usr/bin/python
# SQD Script Test v1.1 for NetApp
# Author :          Yan Ning
# Required package: crontab, expiringdict, stopit
# Note:             This script has to run as root
from crontab import CronTab
from expiringdict import ExpiringDict

import datetime
import getopt
import os
import re
import stopit
import sys
import threading
import time

# List of interested failure keys; add more if needed
FAILURES = ['ERROR', 'WARNING', 'WARN']
# Rotate report file after this number of touches
TOUCH_COUNT_FILE_ROTATE = 15
# Interval to report log status; default to 7 minutes
REPORT_INTERVAL = 7*60


def timer(interval):
    """A timer will invoke the wrapped method at specified interval

    :param interval int: Number of seconds this timer will be on
    :return:
    """
    def decorator(function):
        def wrapper(*args, **kwargs):
            stopped = threading.Event()

            def loop():
                while not stopped.wait(interval):
                    function(*args, **kwargs)

            t = threading.Thread(target=loop)
            t.daemon = True
            t.start()
            return stopped
        return wrapper
    return decorator


@timer(REPORT_INTERVAL)
def report(touches, filename):
    """ Report touch status to a log file.

    :param: touches expiringdict.ExpiringDict: Dict to cached cron job records
    :param: filename string: file name where to store report
    """
    print "Run report now..."
    with open(filename, 'a+') as f:
        f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S\t"))
        f.writelines(str(len(touches))+'\n')


def createCronJob(cmd, minutes, comment):
    """ create cron job to touch file.

    :param cmd: command for cron job
    :param minutes: user input job frequency
    :param comment: cron job comment
    """
    # Need to support other user in the future via sudo
    cron = CronTab(user='root')

    # Check if SDQScript test cron job exists
    if cron.find_comment(comment):
        cron.remove_all(comment=comment)
        cron.write()

    cron_job = cron.new(cmd, comment=comment)
    cron_job.minute.every(minutes)
    cron.write()


def createRotatingLog(filename, logfile, file_count):
    """ create rotating log.

    :param filename: user input filename
    :param logfile: user input logfile name
    :param file_count: logfile suffix
    """
    try:
        if not os.path.exists(logfile):
            return os.rename(filename, logfile)
        else:
            return os.rename(filename, logfile + '.' + str(file_count))
    except OSError:
        raise


def scan_syslog(syslog, touchmark, touches, filename, logfile):
    """ Scan log file for interested patterns continuously.

    :param syslog string: full path for syslog
    :param touchmark string: search pattern for interested touch job
    :param touches expiringdict.ExpiringDict: dict to cache cron job records
    :param filename string: file name to report touch status
    :param logfile string: sys log file to scan cron pattern from
    """
    touch_count = 0
    file_count = 0
    with open(syslog, 'r') as f:
        f.seek(0, 2)
        while True:
            line = ''
            while len(line) == 0 or line[-1] != '\n':
                data = f.readline()
                if data == '':
                    # sleep shortly to avoid looping
                    time.sleep(0.3)
                    continue
                line += data
            # Got a line to process
            if process_line(line, touchmark, touches, filename):
                touch_count += 1

            if touch_count == TOUCH_COUNT_FILE_ROTATE:
                # Time to rotate the report file
                createRotatingLog(filename, logfile, file_count)
                file_count += 1
                touch_count = 0
                pass


def process_line(line, touchmark, touches, filename):
    """ Process each touch job line for interested pattern.
    Report failed record if found.

    :param line string: touch job line in syslog
    :param touchmark string: search pattern for touch job
    :param touches expiringdict.ExpiringDict: dict to cache touch job records
    :return: true if this is a touch line
    """
    if touchmark in line:
        touches[time.time()] = line
        return True

    failed = False
    for failure in FAILURES:
        if failure in line:
            failed = True

    if failed:
        # Write failed line to the test file
        with open(filename, 'a+') as f:
            f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S\t"))
            f.writelines(line)
    return False


def main(argv):
    """Main method that starts up all tasks

    :param argv: command line arguments
    """
    try:
        opts, args = getopt.getopt(argv, "hf:m:d:l:r:")
    except getopt.GetoptError:
        print 'SDQScriptTest.py -f <full path to file name> -m <touch file frequency' \
              ' in minutes> -d <log file dir> -l <log file name> -r <script run time' \
              ' in minutes>'
        sys.exit(2)

    filename = ''
    minutes = ''
    logdir = ''
    logfile = ''
    runtime = ''
    # A dict to cache all touch records found during report interval
    touches = ExpiringDict(max_len=1000, max_age_seconds=REPORT_INTERVAL)
    # Comment for cron job
    comment = 'SDQScript'

    for opt, arg in opts:
        if opt == '-h':
            print 'SDQScriptTest.py -f <report file name (full path)> -m <touch file frequency>' \
                  ' -d <log file dir> -l <log file name> -r <script run time>'
            sys.exit()
        if opt in ('-f', '--filename'):
            filename = arg
            touchmark = '(touch ' + filename + ' # ' + comment + ')'
        if opt == '-m':
            minutes = arg
        if opt in ('-d', '--logdir'):
            logdir = arg
        if opt in ('-l', '--logfile'):
            logfile = arg
        if opt == '-r':
            runtime = arg

    runtime = int(runtime)

    # Check log dir existence
    if not os.path.exists(logdir):
        try:
            os.makedirs(logdir)
        except OSError:
            pass

    # Check log files existence
    if os.listdir(logdir) != []:
        try:
            for f in os.listdir(logdir):
                if re.search(logfile, f):
                    os.remove(os.path.join(logdir, f))
        except OSError:
            raise

    logfile = os.path.join(logdir, logfile)

    # Check file existence
    if os.path.isfile(filename):
        try:
            os.remove(filename)
        except OSError:
            raise

    cmd = ' touch ' + filename
    # Create cron job
    createCronJob(cmd, minutes, comment)

    if runtime == 0:
        try:
            report(touches, filename)
            scan_syslog('/var/log/syslog', touchmark, touches, filename,
                        logfile)
        except KeyboardInterrupt:
            print "User interrupted"
            pass
    else:
        runtime *= 60

        try:
            with stopit.ThreadingTimeout(runtime, swallow_exc=True) as timeout:
                assert timeout.state == 1
                report(touches, filename)
                scan_syslog('/var/log/syslog', touchmark, touches, filename,
                            logfile)
        except stopit.TimeoutException:
            result = 'exception_seen'
            print 'error: ', result

        if timeout.state == 2:
            print 'success'


if __name__ == "__main__":
        main(sys.argv[1:])
