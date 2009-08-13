/*
 * fsewatcher.c
 * 
 * Preston Holmes
 * preston@ptone.com
 * 
 * 
 * 
 * 
 * 
 * 
 * Derived from:
 * http://osxbook.com/software/fslogger/
 * Copyright (c) 2008 Amit Singh (osxbook.com).
 
 * Source released under the GNU GENERAL PUBLIC LICENSE (GPL) Version 2.0.
 * See http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt for details.
 *
 * Compile (Mac OS X 10.5.x only) as follows:
 *
 * gcc -I/path/to/xnu/bsd -Wall -o fslogger fslogger.c
 * gcc -I/Users/preston/UNIX/src/xnu-1228.7.58/bsd -o fsewatcher fsewatcher.c
 */

#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/types.h>
#include <sys/sysctl.h>
#include <sys/fsevents.h>
#include <pwd.h>
#include <grp.h>

#define PROGNAME "fsewatcher"
#define PROGVERS "0.1"

#define DEV_FSEVENTS     "/dev/fsevents" // the fsevents pseudo-device
#define FSEVENT_BUFSIZ   131072          // buffer for reading from the device
#define EVENT_QUEUE_SIZE 4096            // limited by MAX_KFS_EVENTS

// an event argument
typedef struct kfs_event_arg {
    u_int16_t  type;         // argument type
    u_int16_t  len;          // size of argument data that follows this field
    union {
        struct vnode *vp;
        char         *str;
        void         *ptr;
        int32_t       int32;
        dev_t         dev;
        ino_t         ino;
        int32_t       mode;
        uid_t         uid;
        gid_t         gid;
        uint64_t      timestamp;
    } data;
} kfs_event_arg_t;

#define KFS_NUM_ARGS  FSE_MAX_ARGS

// an event
typedef struct kfs_event {
    int32_t         type; // event type
    pid_t           pid;  // pid of the process that performed the operation
    kfs_event_arg_t args[KFS_NUM_ARGS]; // event arguments
} kfs_event;

// event names
static const char *kfseNames[] = {
    "FSE_CREATE_FILE",
    "FSE_DELETE",
    "FSE_STAT_CHANGED",
    "FSE_RENAME",
    "FSE_CONTENT_MODIFIED",
    "FSE_EXCHANGE",
    "FSE_FINDER_INFO_CHANGED",
    "FSE_CREATE_DIR",
    "FSE_CHOWN",
    "FSE_XATTR_MODIFIED",
    "FSE_XATTR_REMOVED",
};

// argument names
static const char *kfseArgNames[] = {
    "FSE_ARG_UNKNOWN", "FSE_ARG_VNODE", "FSE_ARG_STRING", "FSE_ARGPATH",
    "FSE_ARG_INT32",   "FSE_ARG_INT64", "FSE_ARG_RAW",    "FSE_ARG_INO",
    "FSE_ARG_UID",     "FSE_ARG_DEV",   "FSE_ARG_MODE",   "FSE_ARG_GID",
    "FSE_ARG_FINFO",
};

// for pretty-printing of vnode types
enum vtype {
    VNON, VREG, VDIR, VBLK, VCHR, VLNK, VSOCK, VFIFO, VBAD, VSTR, VCPLX
};

enum vtype iftovt_tab[] = {
    VNON, VFIFO, VCHR, VNON, VDIR,  VNON, VBLK, VNON,
    VREG, VNON,  VLNK, VNON, VSOCK, VNON, VNON, VBAD,
};

static const char *vtypeNames[] = {
    "VNON",  "VREG",  "VDIR", "VBLK", "VCHR", "VLNK",
    "VSOCK", "VFIFO", "VBAD", "VSTR", "VCPLX",
};
#define VTYPE_MAX (sizeof(vtypeNames)/sizeof(char *))

static char *
get_proc_name(pid_t pid)
{
    size_t        len = sizeof(struct kinfo_proc);
    static int    name[] = { CTL_KERN, KERN_PROC, KERN_PROC_PID, 0 };
    static struct kinfo_proc kp;

    name[3] = pid;

    kp.kp_proc.p_comm[0] = '\0';
    if (sysctl((int *)name, sizeof(name)/sizeof(*name), &kp, &len, NULL, 0))
        return "?";

    if (kp.kp_proc.p_comm[0] == '\0')
        return "exited?";

    return kp.kp_proc.p_comm;
}

int
main(int argc, char **argv)
{
    int32_t arg_id;
    int     fd, clonefd = -1;
    int     i, j, eoff, off, ret;

    kfs_event_arg_t *kea;
    struct           fsevent_clone_args fca;
    char             buffer[FSEVENT_BUFSIZ];
    struct passwd   *p;
    struct group    *g;
    mode_t           va_mode;
    u_int32_t        va_type;
    u_int32_t        is_fse_arg_vnode = 0;
    char             fileModeString[11 + 1];
    int8_t           event_list[] = { // action to take for each event
                         FSE_REPORT,  // FSE_CREATE_FILE,
                         FSE_REPORT,  // FSE_DELETE,
                         FSE_REPORT,  // FSE_STAT_CHANGED,
                         FSE_REPORT,  // FSE_RENAME,
                         FSE_REPORT,  // FSE_CONTENT_MODIFIED,
                         FSE_REPORT,  // FSE_EXCHANGE,
                         FSE_REPORT,  // FSE_FINDER_INFO_CHANGED,
                         FSE_REPORT,  // FSE_CREATE_DIR,
                         FSE_REPORT,  // FSE_CHOWN,
                         FSE_REPORT,  // FSE_XATTR_MODIFIED,
                         FSE_REPORT,  // FSE_XATTR_REMOVED,
                     };

    if (argc != 1) {
        fprintf(stderr, "%s (%s)\n", PROGNAME, PROGVERS);
        fprintf(stderr, "File system change logger for Mac OS X. Usage:\n");
        fprintf(stderr, "\n\t%s\n\n", PROGNAME);
        fprintf(stderr, "%s does not take any arguments. "
                        "It must be run as root.\n\n", PROGNAME);
        printf("Please report bugs using the following contact information:\n"
           "<URL:http://www.osxbook.com/software/bugs/>\n");

        exit(1);
        exit(1);
    }

    if (geteuid() != 0) {
        fprintf(stderr, "You must be root to run %s. Try again using 'sudo'.\n",
                PROGNAME);
        exit(1);
    }

    setbuf(stdout, NULL);

    if ((fd = open(DEV_FSEVENTS, O_RDONLY)) < 0) {
        perror("open");
        exit(1);
    }

    fca.event_list = (int8_t *)event_list;
    fca.num_events = sizeof(event_list)/sizeof(int8_t);
    fca.event_queue_depth = EVENT_QUEUE_SIZE;
    fca.fd = &clonefd; 
    if ((ret = ioctl(fd, FSEVENTS_CLONE, (char *)&fca)) < 0) {
        perror("ioctl");
        close(fd);
        exit(1);
    }

    close(fd);

    if ((ret = ioctl(clonefd, FSEVENTS_WANT_EXTENDED_INFO, NULL)) < 0) {
        perror("ioctl");
        close(clonefd);
        exit(1);
    }
   // char string2[20]="red dwarf";
   // char string1[20]="";
   //  strcpy(string1, string2);
    char lastpath[300] = "blank";
    char currentpath[300];
    while (1) { // event processing loop
        ret = read(clonefd, buffer, FSEVENT_BUFSIZ);

        off = 0;

        while (off < ret) { // process one or more events received

            struct kfs_event *kfse = (struct kfs_event *)((char *)buffer + off);

            off += sizeof(int32_t) + sizeof(pid_t); // type + pid

            if (kfse->type == FSE_EVENTS_DROPPED) { // special event
                fprintf(stderr, "  %-14s = %s\n", "type", "EVENTS DROPPED");
                exit(1);
            }

            int32_t atype = kfse->type & FSE_TYPE_MASK;
            uint32_t aflags = FSE_GET_FLAGS(kfse->type);

            if ((atype < FSE_MAX_EVENTS) && (atype >= -1)) {

                if (aflags & FSE_COMBINED_EVENTS) {
                    fprintf(stderr,"%s", ", combined events");
                    exit(1);
                }
                if (aflags & FSE_CONTAINS_DROPPED_EVENTS) {
                    fprintf(stderr,"%s", ", contains dropped events");
                    exit(1);
                }
            } else { // should never happen
                printf("This may be a program bug (type = %d).\n", atype);
                exit(1);
            }

            kea = kfse->args; 
            i = 0;

            while (off < ret) {  // process arguments

                i++;

                if (kea->type == FSE_ARG_DONE) { // no more arguments
                    // printf("    %s (%#x)\n", "FSE_ARG_DONE", kea->type);
                    off += sizeof(u_int16_t);
                    break;
                }

                eoff = sizeof(kea->type) + sizeof(kea->len) + kea->len;
                off += eoff;

                arg_id = (kea->type > FSE_MAX_ARGS) ? 0 : kea->type;
                // printf("    %-16s%4hd  ", kfseArgNames[arg_id], kea->len);
                
                
                // switch kfseNames[atype]
                // printf("%d ", kfse->pid);
                
                if (kea->type == FSE_ARG_STRING) { // handle based on argument type
                    strcpy (currentpath,(char *)&(kea->data.str));
                    //  OR atype == FSE_RENAME
                    // strcmp(currentpath, lastpath) != 0
                    if (    atype == FSE_RENAME || 
                            atype == FSE_CREATE_FILE || 
                            atype == FSE_CREATE_DIR || 
                            strcmp(currentpath, lastpath) != 0) {
                        printf("%d\t%s\t%s\t%s\n", kfse->pid, get_proc_name(kfse->pid), kfseNames[atype], currentpath);
                    }
                    strcpy (lastpath,currentpath);
                }

                kea = (kfs_event_arg_t *)((char *)kea + eoff); // next
            } // for each argument
        } // for each event
    } // forever

    close(clonefd);

    exit(0);
}
