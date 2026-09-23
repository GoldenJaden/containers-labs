#define _GNU_SOURCE

#include <errno.h>
#include <inttypes.h>
#include <json-c/json.h>
#include <seccomp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/utsname.h>
#include <unistd.h>

#include <sys/capability.h>

/* ------------------------------------------------------------
 * Utilities
 * ------------------------------------------------------------ */

static void die(const char *msg)
{
    perror(msg);
    exit(EXIT_FAILURE);
}

static void die_msg(const char *msg)
{
    fprintf(stderr, "%s\n", msg);
    exit(EXIT_FAILURE);
}

static void die_seccomp(const char *what, int rc)
{
    fprintf(stderr, "%s: %s\n", what, strerror(-rc));
    exit(EXIT_FAILURE);
}

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage:\n"
        "  %s [OPTIONS] -- PROGRAM [ARGS...]\n"
        "\n"
        "CLI policy mode:\n"
        "  --deny SYSCALL      Deny syscall with EPERM\n"
        "  --deny-all          Deny everything by default\n"
        "  --allow SYSCALL     Allow syscall (used with --deny-all)\n"
        "\n"
        "Profile mode:\n"
        "  --profile FILE      Load Docker/OCI-style seccomp JSON\n"
        "\n"
        "Examples:\n"
        "  %s --deny ptrace --deny mount -- /bin/bash\n"
        "  %s --deny-all --allow read --allow write -- /bin/true\n"
        "  %s --profile default.json -- /bin/bash\n",
        prog, prog, prog, prog);

    exit(EXIT_FAILURE);
}


/* ------------------------------------------------------------
 * Kernel version handling
 * ------------------------------------------------------------ */

struct kernel_version {
    unsigned major;
    unsigned minor;
    unsigned patch;
};

static struct kernel_version get_kernel_version(void)
{
    struct utsname u;
    struct kernel_version v = {0, 0, 0};

    if (uname(&u) < 0)
        die("uname");

    sscanf(u.release, "%u.%u.%u",
           &v.major,
           &v.minor,
           &v.patch);

    return v;
}

static int kernel_at_least(const char *required)
{
    unsigned major = 0, minor = 0, patch = 0;

    sscanf(required, "%u.%u.%u",
           &major, &minor, &patch);

    struct kernel_version current = get_kernel_version();

    if (current.major != major)
        return current.major > major;

    if (current.minor != minor)
        return current.minor > minor;

    return current.patch >= patch;
}


/* ------------------------------------------------------------
 * Runtime architecture
 *
 * Docker's includes.arches uses Go-style names:
 *
 *   amd64
 *   arm64
 *   arm
 *   x86
 *   s390x
 *   ppc64le
 *   riscv64
 *   ...
 * ------------------------------------------------------------ */

 static const char *runtime_arch(void)
 {
     static char machine[256];
 
     struct utsname u;
 
     if (uname(&u) < 0)
         die("uname");
 
     if (!strcmp(u.machine, "x86_64"))
         return "amd64";
 
     if (!strcmp(u.machine, "i386") ||
         !strcmp(u.machine, "i486") ||
         !strcmp(u.machine, "i586") ||
         !strcmp(u.machine, "i686"))
         return "x86";
 
     if (!strcmp(u.machine, "aarch64"))
         return "arm64";
 
     if (!strncmp(u.machine, "arm", 3))
         return "arm";
 
     if (!strcmp(u.machine, "s390x"))
         return "s390x";
 
     if (!strcmp(u.machine, "s390"))
         return "s390";
 
     if (!strcmp(u.machine, "ppc64le"))
         return "ppc64le";
 
     if (!strcmp(u.machine, "ppc64"))
         return "ppc64";
 
     if (!strcmp(u.machine, "riscv64"))
         return "riscv64";
 
     if (!strcmp(u.machine, "loongarch64"))
         return "loong64";
 
     snprintf(machine, sizeof(machine), "%s", u.machine);
     return machine;
 }


/* ------------------------------------------------------------
 * Capability handling
 * ------------------------------------------------------------ */

static int process_has_cap(const char *name)
{
    cap_value_t value;
    cap_flag_value_t result;

    if (cap_from_name(name, &value) != 0) {
        fprintf(stderr,
                "warning: unknown capability: %s\n",
                name);
        return 0;
    }

    cap_t caps = cap_get_proc();

    if (!caps)
        die("cap_get_proc");

    if (cap_get_flag(caps,
                     value,
                     CAP_EFFECTIVE,
                     &result) != 0) {
        cap_free(caps);
        die("cap_get_flag");
    }

    cap_free(caps);

    return result == CAP_SET;
}


/* ------------------------------------------------------------
 * JSON helpers
 * ------------------------------------------------------------ */

static struct json_object *json_get(
    struct json_object *obj,
    const char *key)
{
    struct json_object *out = NULL;

    if (!obj)
        return NULL;

    if (!json_object_object_get_ex(obj, key, &out))
        return NULL;

    return out;
}

static const char *json_get_string_optional(
    struct json_object *obj,
    const char *key)
{
    struct json_object *v = json_get(obj, key);

    if (!v)
        return NULL;

    return json_object_get_string(v);
}

static uint64_t json_get_u64_default(
    struct json_object *obj,
    const char *key,
    uint64_t def)
{
    struct json_object *v = json_get(obj, key);

    if (!v)
        return def;

    return json_object_get_uint64(v);
}


/* ------------------------------------------------------------
 * includes / excludes
 * ------------------------------------------------------------ */

static int string_array_contains(
    struct json_object *array,
    const char *needle)
{
    if (!array ||
        !json_object_is_type(array,
                             json_type_array))
        return 0;

    size_t n = json_object_array_length(array);

    for (size_t i = 0; i < n; i++) {
        struct json_object *item =
            json_object_array_get_idx(array, i);

        const char *s =
            json_object_get_string(item);

        if (s && !strcmp(s, needle))
            return 1;
    }

    return 0;
}


/*
 * For includes:
 *
 * every category present must match.
 *
 * arches: runtime arch must be one of them.
 * caps:   all listed capabilities must be effective.
 * minKernel: kernel must be >= requested version.
 */
static int matches_includes(struct json_object *inc)
{
    if (!inc)
        return 1;

    struct json_object *arches = json_get(inc, "arches");

    if (arches) {
        if (!string_array_contains(arches,
                                   runtime_arch()))
            return 0;
    }

    struct json_object *caps = json_get(inc, "caps");

    if (caps) {
        size_t n = json_object_array_length(caps);

        for (size_t i = 0; i < n; i++) {
            const char *cap =
                json_object_get_string(
                    json_object_array_get_idx(caps, i));

            if (!process_has_cap(cap))
                return 0;
        }
    }

    const char *min_kernel =
        json_get_string_optional(inc, "minKernel");

    if (min_kernel &&
        !kernel_at_least(min_kernel))
        return 0;

    return 1;
}


/*
 * For excludes:
 *
 * a match in any category excludes the rule.
 *
 * This matters for rules such as Docker's clone rule:
 *
 *   excludes:
 *     caps:   CAP_SYS_ADMIN
 *     arches: s390/s390x
 */
static int matches_excludes(struct json_object *exc)
{
    if (!exc)
        return 0;

    struct json_object *arches = json_get(exc, "arches");

    if (arches &&
        string_array_contains(arches,
                              runtime_arch()))
        return 1;

    struct json_object *caps = json_get(exc, "caps");

    if (caps) {
        size_t n = json_object_array_length(caps);

        for (size_t i = 0; i < n; i++) {
            const char *cap =
                json_object_get_string(
                    json_object_array_get_idx(caps, i));

            if (process_has_cap(cap))
                return 1;
        }
    }

    const char *min_kernel =
        json_get_string_optional(exc, "minKernel");

    if (min_kernel &&
        kernel_at_least(min_kernel))
        return 1;

    return 0;
}

static int rule_applies(struct json_object *rule)
{
    if (!matches_includes(json_get(rule, "includes")))
        return 0;

    if (matches_excludes(json_get(rule, "excludes")))
        return 0;

    return 1;
}


/* ------------------------------------------------------------
 * Architecture mapping
 * ------------------------------------------------------------ */

static uint32_t profile_arch(const char *name)
{
#ifdef SCMP_ARCH_X86
    if (!strcmp(name, "SCMP_ARCH_X86"))
        return SCMP_ARCH_X86;
#endif

#ifdef SCMP_ARCH_X86_64
    if (!strcmp(name, "SCMP_ARCH_X86_64"))
        return SCMP_ARCH_X86_64;
#endif

#ifdef SCMP_ARCH_X32
    if (!strcmp(name, "SCMP_ARCH_X32"))
        return SCMP_ARCH_X32;
#endif

#ifdef SCMP_ARCH_ARM
    if (!strcmp(name, "SCMP_ARCH_ARM"))
        return SCMP_ARCH_ARM;
#endif

#ifdef SCMP_ARCH_AARCH64
    if (!strcmp(name, "SCMP_ARCH_AARCH64"))
        return SCMP_ARCH_AARCH64;
#endif

#ifdef SCMP_ARCH_MIPS
    if (!strcmp(name, "SCMP_ARCH_MIPS"))
        return SCMP_ARCH_MIPS;
#endif

#ifdef SCMP_ARCH_MIPS64
    if (!strcmp(name, "SCMP_ARCH_MIPS64"))
        return SCMP_ARCH_MIPS64;
#endif

#ifdef SCMP_ARCH_MIPS64N32
    if (!strcmp(name, "SCMP_ARCH_MIPS64N32"))
        return SCMP_ARCH_MIPS64N32;
#endif

#ifdef SCMP_ARCH_MIPSEL
    if (!strcmp(name, "SCMP_ARCH_MIPSEL"))
        return SCMP_ARCH_MIPSEL;
#endif

#ifdef SCMP_ARCH_MIPSEL64
    if (!strcmp(name, "SCMP_ARCH_MIPSEL64"))
        return SCMP_ARCH_MIPSEL64;
#endif

#ifdef SCMP_ARCH_MIPSEL64N32
    if (!strcmp(name, "SCMP_ARCH_MIPSEL64N32"))
        return SCMP_ARCH_MIPSEL64N32;
#endif

#ifdef SCMP_ARCH_PPC
    if (!strcmp(name, "SCMP_ARCH_PPC"))
        return SCMP_ARCH_PPC;
#endif

#ifdef SCMP_ARCH_PPC64
    if (!strcmp(name, "SCMP_ARCH_PPC64"))
        return SCMP_ARCH_PPC64;
#endif

#ifdef SCMP_ARCH_PPC64LE
    if (!strcmp(name, "SCMP_ARCH_PPC64LE"))
        return SCMP_ARCH_PPC64LE;
#endif

#ifdef SCMP_ARCH_S390
    if (!strcmp(name, "SCMP_ARCH_S390"))
        return SCMP_ARCH_S390;
#endif

#ifdef SCMP_ARCH_S390X
    if (!strcmp(name, "SCMP_ARCH_S390X"))
        return SCMP_ARCH_S390X;
#endif

#ifdef SCMP_ARCH_RISCV64
    if (!strcmp(name, "SCMP_ARCH_RISCV64"))
        return SCMP_ARCH_RISCV64;
#endif

#ifdef SCMP_ARCH_LOONGARCH64
    if (!strcmp(name, "SCMP_ARCH_LOONGARCH64"))
        return SCMP_ARCH_LOONGARCH64;
#endif

    return 0;
}

static void add_arch(
    scmp_filter_ctx ctx,
    const char *name)
{
    uint32_t arch = profile_arch(name);

    if (!arch) {
        fprintf(stderr,
                "warning: unsupported architecture: %s\n",
                name);
        return;
    }

    if (arch == seccomp_arch_native())
        return;

    int rc = seccomp_arch_add(ctx, arch);

    if (rc == -EEXIST)
        return;

    if (rc < 0)
        die_seccomp("seccomp_arch_add", rc);
}


/*
 * Handle OCI architectures[].
 */
static void load_architectures(
    scmp_filter_ctx ctx,
    struct json_object *root)
{
    struct json_object *arches =
        json_get(root, "architectures");

    if (!arches ||
        !json_object_is_type(arches,
                             json_type_array))
        return;

    size_t n = json_object_array_length(arches);

    for (size_t i = 0; i < n; i++) {
        const char *name =
            json_object_get_string(
                json_object_array_get_idx(arches, i));

        if (name)
            add_arch(ctx, name);
    }
}


/*
 * Docker/Moby uses archMap rather than a flat list.
 *
 * We find the entry corresponding to our native arch and
 * enable its compat sub-architectures.
 */
static void load_arch_map(
    scmp_filter_ctx ctx,
    struct json_object *root)
{
    struct json_object *map =
        json_get(root, "archMap");

    if (!map ||
        !json_object_is_type(map,
                             json_type_array))
        return;

    uint32_t native = seccomp_arch_native();
    size_t n = json_object_array_length(map);

    for (size_t i = 0; i < n; i++) {
        struct json_object *entry =
            json_object_array_get_idx(map, i);

        const char *arch_name =
            json_get_string_optional(
                entry,
                "architecture");

        if (!arch_name)
            continue;

        uint32_t arch = profile_arch(arch_name);

        if (!arch || arch != native)
            continue;

        struct json_object *subs =
            json_get(entry, "subArchitectures");

        if (!subs ||
            !json_object_is_type(subs,
                                 json_type_array))
            return;

        size_t sn =
            json_object_array_length(subs);

        for (size_t j = 0; j < sn; j++) {
            const char *sub =
                json_object_get_string(
                    json_object_array_get_idx(
                        subs, j));

            if (sub)
                add_arch(ctx, sub);
        }

        return;
    }
}


/* ------------------------------------------------------------
 * seccomp actions
 * ------------------------------------------------------------ */

static uint32_t parse_action(
    const char *name,
    uint32_t data)
{
    if (!strcmp(name, "SCMP_ACT_ALLOW"))
        return SCMP_ACT_ALLOW;

    if (!strcmp(name, "SCMP_ACT_KILL") ||
        !strcmp(name, "SCMP_ACT_KILL_THREAD"))
        return SCMP_ACT_KILL_THREAD;

#ifdef SCMP_ACT_KILL_PROCESS
    if (!strcmp(name, "SCMP_ACT_KILL_PROCESS"))
        return SCMP_ACT_KILL_PROCESS;
#endif

    if (!strcmp(name, "SCMP_ACT_TRAP"))
        return SCMP_ACT_TRAP;

    if (!strcmp(name, "SCMP_ACT_ERRNO"))
        return SCMP_ACT_ERRNO(data);

    if (!strcmp(name, "SCMP_ACT_TRACE"))
        return SCMP_ACT_TRACE(data);

#ifdef SCMP_ACT_LOG
    if (!strcmp(name, "SCMP_ACT_LOG"))
        return SCMP_ACT_LOG;
#endif

#ifdef SCMP_ACT_NOTIFY
    if (!strcmp(name, "SCMP_ACT_NOTIFY"))
        return SCMP_ACT_NOTIFY;
#endif

    fprintf(stderr,
            "unsupported seccomp action: %s\n",
            name);

    exit(EXIT_FAILURE);
}


/* ------------------------------------------------------------
 * Comparison operators
 * ------------------------------------------------------------ */

static enum scmp_compare parse_cmp(const char *name)
{
    if (!strcmp(name, "SCMP_CMP_NE"))
        return SCMP_CMP_NE;

    if (!strcmp(name, "SCMP_CMP_LT"))
        return SCMP_CMP_LT;

    if (!strcmp(name, "SCMP_CMP_LE"))
        return SCMP_CMP_LE;

    if (!strcmp(name, "SCMP_CMP_EQ"))
        return SCMP_CMP_EQ;

    if (!strcmp(name, "SCMP_CMP_GE"))
        return SCMP_CMP_GE;

    if (!strcmp(name, "SCMP_CMP_GT"))
        return SCMP_CMP_GT;

    if (!strcmp(name, "SCMP_CMP_MASKED_EQ"))
        return SCMP_CMP_MASKED_EQ;

    fprintf(stderr,
            "unsupported comparison operator: %s\n",
            name);

    exit(EXIT_FAILURE);
}


/* ------------------------------------------------------------
 * Add one syscall rule
 * ------------------------------------------------------------ */

static int add_syscall_rule(
    scmp_filter_ctx ctx,
    struct json_object *rule,
    const char *syscall_name,
    uint32_t action)
{
    int nr =
        seccomp_syscall_resolve_name(syscall_name);

    /*
     * This is important for Docker profiles:
     * profiles often contain syscalls unknown to an older
     * kernel/libseccomp.
     */
    if (nr == __NR_SCMP_ERROR) {
        fprintf(stderr,
                "warning: unknown syscall: %s\n",
                syscall_name);
        return 0;
    }

    struct json_object *args =
        json_get(rule, "args");

    if (!args ||
        !json_object_is_type(args,
                             json_type_array) ||
        json_object_array_length(args) == 0) {

        int rc =
            seccomp_rule_add(ctx,
                             action,
                             nr,
                             0);

        /*
         * libseccomp may reject rules equivalent to the
         * default action. They are unnecessary anyway.
         */
        if (rc == -EACCES)
            return 0;

        return rc;
    }

    size_t argc =
        json_object_array_length(args);

    if (argc > 6) {
        fprintf(stderr,
                "too many syscall arguments in rule for %s\n",
                syscall_name);
        return -EINVAL;
    }

    struct scmp_arg_cmp cmps[6];

    memset(cmps, 0, sizeof(cmps));

    for (size_t i = 0; i < argc; i++) {
        struct json_object *arg =
            json_object_array_get_idx(args, i);

        unsigned index =
            (unsigned)json_get_u64_default(
                arg, "index", 0);

        if (index > 5) {
            fprintf(stderr,
                    "%s: invalid argument index %u\n",
                    syscall_name,
                    index);
            return -EINVAL;
        }

        uint64_t value =
            json_get_u64_default(
                arg, "value", 0);

        /*
         * Docker profiles use valueTwo.
         * Some other serializers use value_two.
         */
        uint64_t value_two;

        if (json_get(arg, "valueTwo"))
            value_two =
                json_get_u64_default(
                    arg, "valueTwo", 0);
        else
            value_two =
                json_get_u64_default(
                    arg, "value_two", 0);

        const char *op_name =
            json_get_string_optional(
                arg, "op");

        if (!op_name) {
            fprintf(stderr,
                    "%s: syscall argument has no op\n",
                    syscall_name);
            return -EINVAL;
        }

        enum scmp_compare op =
            parse_cmp(op_name);

        cmps[i].arg = index;
        cmps[i].op = op;
        cmps[i].datum_a = value;
        cmps[i].datum_b = value_two;
    }

    int rc =
        seccomp_rule_add_array(
            ctx,
            action,
            nr,
            (unsigned)argc,
            cmps);

    if (rc == -EACCES)
        return 0;

    return rc;
}


/* ------------------------------------------------------------
 * Docker/OCI profile loader
 * ------------------------------------------------------------ */

static scmp_filter_ctx load_profile(
    const char *path)
{
    struct json_object *root =
        json_object_from_file(path);

    if (!root) {
        fprintf(stderr,
                "cannot read or parse seccomp profile: %s\n",
                path);
        exit(EXIT_FAILURE);
    }

    const char *default_name =
        json_get_string_optional(
            root,
            "defaultAction");

    if (!default_name) {
        json_object_put(root);
        die_msg(
            "seccomp profile has no defaultAction");
    }

    uint32_t default_errno =
        (uint32_t)json_get_u64_default(
            root,
            "defaultErrnoRet",
            EPERM);

    uint32_t default_action =
        parse_action(
            default_name,
            default_errno);

    scmp_filter_ctx ctx =
        seccomp_init(default_action);

    if (!ctx) {
        json_object_put(root);
        die_msg("seccomp_init failed");
    }

    /*
     * OCI-style architectures and Docker-style archMap.
     */
    load_architectures(ctx, root);
    load_arch_map(ctx, root);

    struct json_object *syscalls =
        json_get(root, "syscalls");

    if (!syscalls) {
        json_object_put(root);
        return ctx;
    }

    if (!json_object_is_type(syscalls,
                             json_type_array)) {
        json_object_put(root);
        seccomp_release(ctx);
        die_msg("syscalls must be an array");
    }

    size_t rule_count =
        json_object_array_length(syscalls);

    for (size_t i = 0; i < rule_count; i++) {
        struct json_object *rule =
            json_object_array_get_idx(
                syscalls, i);

        if (!rule_applies(rule))
            continue;

        const char *action_name =
            json_get_string_optional(
                rule,
                "action");

        if (!action_name) {
            fprintf(stderr,
                    "warning: syscall rule without action\n");
            continue;
        }

        uint32_t errno_ret =
            (uint32_t)json_get_u64_default(
                rule,
                "errnoRet",
                default_errno);

        uint32_t action =
            parse_action(
                action_name,
                errno_ret);

        struct json_object *names =
            json_get(rule, "names");

        /*
         * Older profile formats sometimes use singular "name".
         */
        if (!names) {
            const char *single =
                json_get_string_optional(
                    rule,
                    "name");

            if (single) {
                int rc =
                    add_syscall_rule(
                        ctx,
                        rule,
                        single,
                        action);

                if (rc < 0) {
                    fprintf(stderr,
                            "cannot add %s: %s\n",
                            single,
                            strerror(-rc));
                    seccomp_release(ctx);
                    json_object_put(root);
                    exit(EXIT_FAILURE);
                }
            }

            continue;
        }

        if (!json_object_is_type(
                names,
                json_type_array)) {
            fprintf(stderr,
                    "warning: names is not an array\n");
            continue;
        }

        size_t name_count =
            json_object_array_length(names);

        for (size_t j = 0; j < name_count; j++) {
            const char *name =
                json_object_get_string(
                    json_object_array_get_idx(
                        names, j));

            if (!name)
                continue;

            int rc =
                add_syscall_rule(
                    ctx,
                    rule,
                    name,
                    action);

            if (rc < 0) {
                fprintf(stderr,
                        "cannot add syscall %s: %s\n",
                        name,
                        strerror(-rc));

                seccomp_release(ctx);
                json_object_put(root);
                exit(EXIT_FAILURE);
            }
        }
    }

    json_object_put(root);

    return ctx;
}


/* ------------------------------------------------------------
 * CLI rules
 * ------------------------------------------------------------ */

enum cli_rule_type {
    CLI_ALLOW,
    CLI_DENY
};

struct cli_rule {
    enum cli_rule_type type;
    const char *name;
};

static void add_cli_rule(
    scmp_filter_ctx ctx,
    enum cli_rule_type type,
    const char *name)
{
    int nr =
        seccomp_syscall_resolve_name(name);

    if (nr == __NR_SCMP_ERROR) {
        fprintf(stderr,
                "unknown syscall: %s\n",
                name);
        exit(EXIT_FAILURE);
    }

    uint32_t action =
        type == CLI_ALLOW
            ? SCMP_ACT_ALLOW
            : SCMP_ACT_ERRNO(EPERM);

    int rc =
        seccomp_rule_add(
            ctx,
            action,
            nr,
            0);

    if (rc == -EACCES)
        return;

    if (rc < 0)
        die_seccomp(
            "seccomp_rule_add",
            rc);
}


/* ------------------------------------------------------------
 * main
 * ------------------------------------------------------------ */

int main(int argc, char **argv)
{
    if (argc < 2)
        usage(argv[0]);

    const char *profile = NULL;
    int deny_all = 0;

    struct cli_rule *rules =
        calloc((size_t)argc,
               sizeof(*rules));

    if (!rules)
        die("calloc");

    size_t nrules = 0;
    int i = 1;

    while (i < argc) {

        if (!strcmp(argv[i], "--")) {
            i++;
            break;
        }

        if (!strcmp(argv[i], "--profile")) {
            i++;

            if (i >= argc)
                usage(argv[0]);

            profile = argv[i++];
            continue;
        }

        if (!strcmp(argv[i], "--deny-all")) {
            deny_all = 1;
            i++;
            continue;
        }

        if (!strcmp(argv[i], "--allow")) {
            i++;

            if (i >= argc)
                usage(argv[0]);

            rules[nrules].type = CLI_ALLOW;
            rules[nrules].name = argv[i];
            nrules++;

            i++;
            continue;
        }

        if (!strcmp(argv[i], "--deny")) {
            i++;

            if (i >= argc)
                usage(argv[0]);

            rules[nrules].type = CLI_DENY;
            rules[nrules].name = argv[i];
            nrules++;

            i++;
            continue;
        }

        fprintf(stderr,
                "unknown option: %s\n",
                argv[i]);

        usage(argv[0]);
    }

    if (i >= argc)
        usage(argv[0]);

    if (profile &&
        (deny_all || nrules != 0)) {
        die_msg(
            "--profile cannot currently be combined "
            "with --allow/--deny/--deny-all");
    }

    scmp_filter_ctx ctx;

    if (profile) {
        ctx = load_profile(profile);
    } else {
        uint32_t default_action =
            deny_all
                ? SCMP_ACT_ERRNO(EPERM)
                : SCMP_ACT_ALLOW;

        ctx = seccomp_init(default_action);

        if (!ctx)
            die_msg("seccomp_init failed");

        for (size_t n = 0;
             n < nrules;
             n++) {

            if (!deny_all &&
                rules[n].type == CLI_ALLOW) {
                fprintf(stderr,
                        "warning: --allow %s is redundant "
                        "without --deny-all\n",
                        rules[n].name);
                continue;
            }

            if (deny_all &&
                rules[n].type == CLI_DENY) {
                fprintf(stderr,
                        "warning: --deny %s is redundant "
                        "with --deny-all\n",
                        rules[n].name);
                continue;
            }

            add_cli_rule(
                ctx,
                rules[n].type,
                rules[n].name);
        }

        /*
         * In CLI deny-all mode the launcher itself needs
         * execve to replace itself with PROGRAM.
         */
        if (deny_all) {
            int rc;

            rc = seccomp_rule_add(
                ctx,
                SCMP_ACT_ALLOW,
                SCMP_SYS(execve),
                0);

            if (rc < 0 && rc != -EACCES)
                die_seccomp(
                    "allow execve",
                    rc);

#ifdef __NR_execveat
            rc = seccomp_rule_add(
                ctx,
                SCMP_ACT_ALLOW,
                SCMP_SYS(execveat),
                0);

            if (rc < 0 && rc != -EACCES)
                die_seccomp(
                    "allow execveat",
                    rc);
#endif
        }
    }

    free(rules);

    /*
     * Required for an unprivileged process to install
     * the filter.
     */
    if (prctl(PR_SET_NO_NEW_PRIVS,
              1, 0, 0, 0) < 0) {
        seccomp_release(ctx);
        die("PR_SET_NO_NEW_PRIVS");
    }

    int rc = seccomp_load(ctx);

    if (rc < 0) {
        seccomp_release(ctx);
        die_seccomp("seccomp_load", rc);
    }

    seccomp_release(ctx);

    execvp(argv[i], &argv[i]);

    perror("execvp");
    return 127;
}