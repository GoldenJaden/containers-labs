#include <errno.h>
#include <seccomp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <unistd.h>

static void fail_libseccomp(const char *operation, int error)
{
	fprintf(stderr, "%s: %s\n", operation, strerror(-error));
	exit(EXIT_FAILURE);
}

int main(int argc, char *argv[])
{
	scmp_filter_ctx filter;
	int rc;

	if (argc < 2) {
		fprintf(stderr, "usage: %s <program> [arguments...]\n", argv[0]);
		return EXIT_FAILURE;
	}

	if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == -1) {
		perror("prctl(PR_SET_NO_NEW_PRIVS)");
		return EXIT_FAILURE;
	}

	filter = seccomp_init(SCMP_ACT_ALLOW);
	if (filter == NULL) {
		fprintf(stderr, "seccomp_init: cannot allocate filter\n");
		return EXIT_FAILURE;
	}

	rc = seccomp_rule_add(filter, SCMP_ACT_ERRNO(EPERM), SCMP_SYS(uname), 0);
	if (rc < 0) {
		seccomp_release(filter);
		fail_libseccomp("seccomp_rule_add(uname)", rc);
	}

	rc = seccomp_load(filter);
	if (rc < 0) {
		seccomp_release(filter);
		fail_libseccomp("seccomp_load", rc);
	}

	seccomp_release(filter);

	execvp(argv[1], &argv[1]);
	perror("execvp");
	return EXIT_FAILURE;
}
