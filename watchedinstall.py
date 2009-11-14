#!/usr/bin/env python
# encoding: utf-8
"""
watchedinstall.py

This script will run the osx command line installer and watch the process for any child
processes that are spawned.
Meanwhile a tool that monitors all changes to the file system is also started

finally the log of all FS changes is filtered for only those changes made by the installer 
or its descendant processes and the output is generated either in radmind or generic format

Changes:
2009-07-15
Initial public release
2009-07-16
no longer check for existance of command file if format is not radmind

todo:
allow convenience reference to command file as in radwrap

Created by Preston Holmes on 2009-03-27.
preston@ptone.com

Copyright (c) 2009 Preston Holmes

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.

Todo:
scan for positive items that are in negative space - right now negative space is ignored
"""

import sys
import os
from subprocess import Popen, call, STDOUT, PIPE
import time
import re
from optparse import OptionParser,OptionGroup
# the following imports only needed for python fsdiff
import xattr
import hashlib
import shutil
import pdb

excludes = []
logfile = '/tmp/events.log'
pidlog = '/tmp/pid_log.log'
unsorted_file = '/tmp/unsorted'
pkg_maker_cmd = '/Developer/Applications/Utilities/PackageMaker.app/Contents/MacOS/PackageMaker'

debug = True

pids = set() # maintains collection of unique values

def sh(cmd):
    return Popen(cmd,shell=True,stdout=PIPE,stderr=PIPE).communicate()[0]

def parse_pidlog(parentPID):
    global pids
    global pidlog
    for i,line in enumerate(open(pidlog)):
        if i == 0: 
            continue # skip the header line
        data = line.split()
        if data[2] in pids:
            pids.add(data[1])
    
def parse_excludes (path):
    global excludes
    for line in open(path):
        if line[0] == 'x':
            p = line.split()[1]\
                .lstrip('.')\
                .replace('.','\\.')\
                .replace('*','.*')
            excludes.append(p)
        elif line[0] == 'k':
            f = os.path.join(os.path.dirname(path), line.split()[1])
            parse_excludes (f)

def get_excludes(options):
    global excludes
    if options.format == 'radmind':
        parse_excludes(options.command_file)
    # default excludes
    excludes.append('\.\.namedfork')
    return [re.compile(s,re.I) for s in excludes]

    
def cleanup():
    if debug: return
    try:
        os.remove(logfile)
        os.remove(unsorted_file)
    except:
        pass
    try:
        call(['launchctl','load','/System/Library/LaunchDaemons/com.apple.metadata.mds.plist'],stdout=PIPE,stderr=PIPE)
    except:
        pass

def pkg_from_transcript(transcript):
    pkg_root = '/tmp/package_root'
    pkg_maker_cmd = '/Developer/Applications/Utilities/PackageMaker.app/Contents/MacOS/PackageMaker'
    if not os.path.exists(pkg_root): os.makedirs(pkg_root)
    os.chdir('/')
    for line in open(transcript):
        fields = line.split()
        abs_path = os.path.abspath(fields[1])
        if os.path.isfile(abs_path):
            dest = os.path.join (pkg_root,os.path.dirname(abs_path)[1:] + '/')
            call(['ditto',abs_path,dest])
    pkg_id = os.path.basename(transcript)
    call([pkg_maker_cmd,'--root',pkg_root,'--id',pkg_id,'--title',pkg_id,'--target','10.4','--out',pkg_id + '.pkg'])
    shutil.rmtree(pkg_root)

def main():

    parser = OptionParser()
    parser.add_option ("-v", "--verbose", action="store_true",
                      help="display all verbose installer output",default=False)
    parser.add_option ('-e','--english-only',action="store_true",default=False,
                        help='filter out non english project files')
    parser.add_option ('-o','--output',dest='out_file',
                        help='file to save results in, if not specified use standard out',metavar='PATH')
    parser.add_option ('-f','--format',
                        help='format for output file, default: radmind', default='radmind',metavar='[radmind | standard | package]')
    parser.add_option ('-i','--pid', help="Manually specify the PID of the parent installer process", metavar="PID")
    
    installer_group = OptionGroup(parser,"Installer Options",
                                "These options apply if you are choosing to invoke Apple's installer with a package")
    installer_group.add_option('-p','--package',dest='installer_package',
                        help='package to install',metavar='PATH')
    installer_group.add_option('-t','--target',dest='installer_target',
                        help='target of package install, defaults to /', default='/',metavar='PATH')
    
    rad_group = OptionGroup(parser, "Radmind Options",
                                "These options only apply if the radmind format is used")
    rad_group.add_option('-K',dest='command_file',
                        help='path to command file, default is standard command.K',metavar="PATH", default='/var/radmind/client/command.K')
    rad_group.add_option('-I',dest='case_insensitive',help='use case insensitive sorting',
                        action="store_true",default=False)
    rad_group.add_option('-C','--comparison-path',dest='comparison_path',
                        help='comparison path to use, default is relative', default='.',metavar='[ . | / ]')
    rad_group.add_option('-c',dest='checksum',help='checksum if any, only sha1 supported for -P option',metavar='[ only sha1 supported ]')
    rad_group.add_option('-P',dest='pythondiff',help='enable experimental pure python fsdiff output (faster)',action="store_true",default=False)
    parser.usage = """
                        watchedinstall.py [options]

                        This script attempts to perform a OS X install 
                        of a package while watching for any filesystem 
                        changes the installer makes.

                        Output can be in the form of a ready 
                        to upload radmind transcript
                        
                        Must be run as root
                        
                        Requires companion fsewatcher tool
                        """

    parser.add_option_group(installer_group)
    parser.add_option_group(rad_group)
    
    (options, args) = parser.parse_args()
    
    # check for options errors
    if not options.installer_package and not options.pid:
        parser.error("either a source package, or a PID is a required argument")
    if options.installer_package and not os.path.exists(options.installer_package):
        parser.error("specified Package could not be found")
    if options.installer_package and  options.pid:
        parser.error("Both an installer package and PID were specified, but the program requires one or the other - not both")
    if options.verbose and not options.out_file:
        sys.stderr.write('WARNING: no output file specified, verbose output disabled, writing to standard out\n')
        options.verbose = False
    if options.format == 'radmind' and not os.path.exists(options.command_file):
        parser.error("specified command file could not be found")
    if options.installer_target != '/':
        options.installer_target = options.installer_target.rstrip('/') # strip trailing /
    if os.geteuid() != 0:
        parser.error ('must be run as root')
    # if call(['which',''])
    if options.format == 'package':
        if not os.path.exists(pkg_maker_cmd):
            parser.error ('packagemaker tool not found - are developer tools installed?')
        if not os.path.exists('/usr/bin/otool') and os.path.exists('/Developer/usr/bin/otool'):
            # fixes a glitch in packagemaker expectations if unix portion of dev tools not installed
            sh('ln -s /Developer/usr/bin/otool /usr/bin/otool')
    if options.format == 'package' and options.installer_target != '/' and options.installer_package:
        parser.error ('package output currently only available for installs on boot volume')
    if options.format == 'radmind' and sh('which fsdiff') == '':
        parser.error ('radmind tools not found') 
    cleanup()

    # the install and parselog steps were made into functions so that 
    # just the parsing step could be debugged and optimised
    def install():
        """Starts a fsevents watching tool that logs all changes, then runs 
        the osx installer, while looping over 
        a PS command to keep track of any descendent PIDs spawned by the installer"""
        global pids
        log_handle = open(logfile,'w')
        pidlog_handle = open(pidlog,'w')
        
        try:
            fs_logger = Popen(['fsewatcher'], stdout=log_handle,shell=True)
        except OSError:
            sys.exit("Unable to run fsewatcher tool, make sure it was properly installed")
        pid_logger = Popen(['execsnoop'],stdout=pidlog_handle,shell=True)
        if options.installer_package:
            installer_command = ['installer','-verbose','-pkg', options.installer_package,'-target', options.installer_target]
            # these environment variable can help convince installer to install on non-boot drive
            os.environ['CM_BUILD'] = 'CM_BUILD'
            os.environ['COMMAND_LINE_INSTALL'] = '1'
            
            
            if options.verbose:
                installer_out = sys.stdout
            else:
                installer_out = PIPE
            if options.verbose:
                print "fsewatcher running - starting installer"
            
            installer = Popen(installer_command, stdout=installer_out, stderr=STDOUT, bufsize=1)
            parentPID = str(installer.pid)
            pids.add(parentPID)
            # disable spotlight
            call(['launchctl','unload','/System/Library/LaunchDaemons/com.apple.metadata.mds.plist'])
            while installer.poll() != 0:
                
                # second implementation - now post process log
                # process_list = Popen("ps -ax -o ppid=,pid=,command=", shell=True, stdout=PIPE,stderr=PIPE).communicate()[0].split('\n')[:-1]
                # for p in process_list:
                #     data = p.split()
                #     if data[0] in pids:
                #         pids.add(data[1])
                
                # first implemenation
                # if loop % 10 == 0:
                #     if options.verbose:
                #         print '\n'.join(installer.stdout.readlines(1024))
                #     # todo check on logger process
                # loop += 1
                time.sleep(1)
            if options.verbose:
                print "installer exited"
            if  installer.returncode:
                errstr = "Installer Failed with return code: %s\n%s" % (installer.returncode,installer.communicate()[0])
                sys.exit(errstr)
        else:
            # using manual PID setting
            parentPID = options.pid
            pids.add(parentPID)
            parent_found = True
            # todo: add execsnoop feature to this part
            while parent_found:
                process_list = Popen("ps -ax -o ppid=,pid=,command=", shell=True, stdout=PIPE,stderr=PIPE).communicate()[0].split('\n')[:-1]
                parent_found = False
                for p in process_list:
                    data = p.split()
                    if data[1] == parentPID:
                        parent_found = True
                        break # for loop
                if not parent_found:
                    break # while loop
                time.sleep(1)
                
        # stop the logger
        if options.verbose:
            print "killing logger processes"
        call(['kill',str (fs_logger.pid)])
        call(['kill',str (pid_logger.pid)])
        log_handle.close()
        pidlog_handle.close()
        parse_pidlog(parentPID)

    def parselog():
        """As efficiently as possible scan the log of FS changes to extract and 
        report on those made by the installer or its descendants."""
        global pids

        unsorted = open(unsorted_file,'w')
        fsdiff_command = ['fsdiff','-1']
        twhich_command = ['twhich']
        radmind_options = []
        if options.case_insensitive:
            radmind_options.append('-I')
        if options.command_file:
            radmind_options.extend(['-K',options.command_file])
        if options.checksum:
            radmind_options.extend(['-c',options.checksum])
        
        os.chdir(options.installer_target)
        # call('say in python loop',shell=True)
        
        def prep_path(path):
            if options.installer_target != '/':
                path = path.replace(options.installer_target, '')
            if options.case_insensitive:
                path = '.' + path
            return path
        
        def path_ok(path):
            if options.english_only:
                if re.search('.lproj',path):
                    if not (re.search('English.lproj',path) or re.search('en.lproj',path)):
                        return False
            for pattern in exclude_patterns:
                if pattern.search(path):
                    return False
            return True
            
        def twhich(path):
            # items_already_output.append(path)
            if path_ok(path):
                path = prep_path(path)
                twhich_result = Popen(twhich_command + radmind_options + [path],stdout=PIPE).communicate()[0]
                # twhich_result = ''
                # print twhich_result
                twhich_lines = twhich_result.split('\n')
                if len (twhich_lines) > 1:
                    if '# Exclude' in twhich_lines[0]:
                        return
                    elif twhich_lines[1][0] != '#' and twhich_lines[1][-1] != ':':
                        unsorted.write('- ' + twhich_lines[1] + '\n')
        
        def fsdiff_b(path):
            """a blank version of fsdiff for test optimizations"""
            return
            
        def fsdiff(path):
            if os.path.exists(path):
                if path_ok(path):
                    path = prep_path(path)
                    Popen(fsdiff_command + radmind_options + [path],stdout=unsorted).wait()

            
        def fsdiff_p(path):
            """ 
            a start at a pure python implementation of the fsdiff output
            """
            # todo relative paths for links
            if os.path.exists(path):
                if path_ok(path):
                    info = os.lstat(path)
                    # ls -lPTne xmas-photo.jpg 
                    # ls_info = Popen(['ls','-lPTne',"'%s'" % path.replace ("'","\\'")])
                    xatrib = xattr.xattr(path)
                    orig_path = path
                    path = prep_path(path)
                    sel_info = [    path.replace(' ','\\b').replace('\t','\\t'),
                                    oct(info.st_mode & 0777), 
                                    info.st_uid,
                                    info.st_gid,
                                    info.st_mtime,
                                    info.st_size,
                                    '-'
                                    ]
                    sel_info = [str(x) for x in sel_info]
                    if xatrib:
                        l = "a " + ' '.join(sel_info)
                    elif os.path.islink(path):
                        l = "l " + path.replace(' ','\\b').replace('\t','\\t') + ' ' +\
                            os.path.realpath(path).replace(' ','\\b').replace('\t','\\t')
                    elif os.path.isfile(path):
                        l = "f " + ' '.join(sel_info)
                    elif os.path.isdir(path):
                        l = "d " + ' '.join(sel_info[:3])
                    # Not yet dug enough into how to get a radmind style cksum in python...
                    # if xatrib or os.path.isfile(path):
                    #     # write checksum
                    #     if options.checksum:
                    #         cksum = hashlib.sha1(open(orig_path,'rb').read()).hexdigest()
                    #         l += cksum 
                    unsorted.write(l + '\n')
        
        def standard_add(path):
            if os.path.exists(path):
                if path_ok(path):
                    unsorted.write("+ %s\n" % path)

            
        def standard_delete(path):
            if path_ok(path):
                unsorted.write("- %s\n" % path)

        
        
        installer_created = {}
        items_already_output = {}
        items_removed = {}
        pids = list(pids)
        last_path = ''
        last_action = ''
        # last_inode = 0
        exclude_patterns = get_excludes(options)
        if options.format == 'radmind':
            if options.pythondiff:
                add = fsdiff_p
            else:
                add = fsdiff
            remove = twhich
        else:
            add = standard_add
            remove = standard_delete
            
        PID = 0
        PROCESS = 1
        EVENT = 2
        PATH = 3
        INODE = 4
        if options.verbose:
            last_percent = 0
            linect = float (Popen(['wc -l ' + logfile],stdout=PIPE,shell=True).\
                communicate()[0].split()[0])
            print "PIDs involved:"
            print pids
            print "scanning %s file system events for installer changes" % int (linect)
            print '%s patterns excluded' % len(exclude_patterns)
        f = open(logfile)
        # pdb.set_trace()
        for lineno,line in enumerate(f):
            if options.verbose:
                percent = int (round(float(lineno)/linect * 100))
                if percent > 0 and percent % 10 == 0 and percent != last_percent:
                    print '%%%s complete' % percent
                    last_percent = percent
                    # print len(items_already_output)/lineno * 100
            if line in ('','\n'): continue # blank line at end
            fields = line.split('\t')
            logged_path = fields[PATH].strip()
            # if len(fields) != 5:
            #     # assuming missing the inode
            #     if os.path.exists(logged_path):
            #         inode = os.lstat(logged_path).st_ino
            #         # print 'inod given for %s' % logged_path
            #     else:
            #         continue
            # else:
            #     inode = int (fields[INODE].strip())
            if fields[PID] in pids and logged_path not in items_already_output:
                # installer related FS change
                # since the installer processes will often make several changes to a file
                # we try to only take action once per file
                if logged_path != last_path:
                    # if last action was rename diff it if it wasn't created by the installer
                    if last_action == 'FSE_RENAME':
                        if last_path not in installer_created and last_path not in items_already_output:
                            # the file being renamed was not created by the installer
                            # print 'removing1 %s' % last_path
                            # remove(last_path)
                            items_removed[last_path] = 1
                        if fields[EVENT] == 'FSE_RENAME':
                            # the current file needs to be noted either way
                            # print 'adding1   %s' % logged_path
                            add(logged_path)
                            items_already_output[logged_path] = 1
                            if logged_path in items_removed:
                                # delete from the list of things to remove
                                del(items_removed[logged_path])
                    elif last_action == 'FSE_DELETE':
                        if last_path not in installer_created:
                            # only probe deleted files if they were not 
                            # temp files created by the installer
                            # print 'removing2 %s' % last_path
                            # remove(last_path)
                            items_removed[last_path] = 1
                    elif last_path not in items_already_output:
                        # any other change to a file - we want to fsdiff it
                        # print 'adding2   %s' % last_path
                        add(last_path)
                        items_already_output[last_path] = 1
                        if last_path in items_removed:
                            del(items_removed[last_path])
                    # if this is the first time we see a file
                    # note if it is being created by the installer
                    if fields[EVENT] in ("FSE_CREATE_FILE","FSE_CREATE_DIR"):
                        installer_created[logged_path] = 1
                        # print 'appending %s' % logged_path
                        # continue
                last_path = logged_path
                last_action = fields[EVENT]
                # last_inode = inode


        for p in items_removed:
            # print 'removing %s' % p
            remove(p)
        f.close()
        unsorted.close()
        if options.format == 'radmind':
            tmpfile = '/tmp/sorted.T'
            lsort_command = ['lsort']
            if options.case_insensitive:
                 lsort_command.append('-I')
            lsort_command.extend(['-o',tmpfile])
            lsort_command.append(unsorted_file) # the unsorted input transcript
            call(lsort_command)
            if add == fsdiff_p and options.checksum:
                # python version of fsdiff still can't do cksums right
                # link the tmp file and install root to fool radmind into doing a lcksum
                t_name = os.path.basename(tmpfile)
                if not os.path.exists('/var/radmind/tmp/transcript'):
                    os.makedirs('/var/radmind/tmp/transcript')
                if not os.path.exists('var/radmind/tmp/file'):
                    os.makedirs('var/radmind/tmp/file')
                if os.path.exists('var/radmind/tmp/file/' + t_name):
                    os.remove('var/radmind/tmp/file/' + t_name)
                os.symlink(options.installer_target,'var/radmind/tmp/file/' + t_name)
                if os.path.exists('/var/radmind/tmp/transcript/' + t_name):
                    os.remove('/var/radmind/tmp/transcript/' + t_name)
                # os.symlink(tmpfile,'/var/radmind/tmp/transcript/' + t_name)
                os.rename(tmpfile,'/var/radmind/tmp/transcript/' + t_name)
                lcksum = ['lcksum']
                if options.case_insensitive:
                    lcksum.append('-I')
                if options.verbose:
                    print "checksumming transcript..."
                lcksum.extend(['-q','-c','sha1','/var/radmind/tmp/transcript/' + t_name])
                call(lcksum)
                os.rename('/var/radmind/tmp/transcript/' + t_name,tmpfile)

            if options.out_file:
                if os.path.isdir(options.out_file) and options.installer_package:
                    # outfile_name = os.path.basename(options.installer_package).replace('.pkg','.T')
                    outfile_name = re.sub ('(.pkg|.mpkg)','.T',os.path.basename(options.installer_package))
                    outfile = os.path.join(options.out_file,outfile_name)
                else:
                    outfile = options.out_file
                os.rename(tmpfile,outfile)

            else:
                call(['cat',tmpfile])
        elif options.format == 'package':
            # create a package from the temp transcript
            # todo refactor this outfile rename bit with radmind format above
            if options.out_file:
                if os.path.isdir(options.out_file) and options.installer_package:
                    # outfile_name = os.path.basename(options.installer_package).replace('.pkg','-repack.pkg')
                    outfile_name = re.sub ('(.pkg|.mpkg)','-repack.pkg',os.path.basename(options.installer_package))
                    outfile = os.path.join(options.out_file,outfile_name)
                else:
                    outfile = options.out_file
            pkg_root = '/tmp/package_root'
            pkg_maker_cmd = '/Developer/Applications/Utilities/PackageMaker.app/Contents/MacOS/PackageMaker'
            if os.path.exists(pkg_root): 
                shutil.rmtree(pkg_root)
            os.makedirs(pkg_root)                
            os.chdir('/')
            for line in open(unsorted_file):
                abs_path = os.path.abspath(line[2:].strip())
                # print type(abs_path)
                if os.path.isfile (abs_path):
                    dest = os.path.join (pkg_root,os.path.dirname(abs_path)[1:] + '/')
                    call(['ditto',abs_path,dest])
            pkg_id = os.path.basename(options.installer_package)
            call([pkg_maker_cmd,'--root',pkg_root,'--id',pkg_id,'--title',pkg_id,'--target','10.4','--out',outfile])
            if not debug: shutil.rmtree(pkg_root)
        else:
            # 'standard' output
            sortcmd = ['sort',unsorted_file]
            if options.out_file:
                sortcmd.extend(['-o',options.out_file])
            call(sortcmd)

    
    try:
        if options.verbose:
            print "Starting Install"
        install()
        
        ## Debugging
        # global pids
        # pids = ['60484']
        # pids = ['71687', '71684', '70596', '71680', '71666', '71688', '71705', '71699', '71691', '71692', '70584', '71703', '71696', '70564', '71674', '70560', '71670', '70532', '70556', '70516', '71707', '70480']
        
        parselog()
        cleanup()
        sys.exit(0)
    except:
        cleanup()
        raise
if __name__ == '__main__':
    main()

