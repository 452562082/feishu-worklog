/*
 * FeishuWorklog.app 的启动器。
 *
 * 为什么需要它：
 *   launchd 直接 exec python 时，macOS 的 TCC（隐私授权）把权限算在 python
 *   头上 —— python 路径带 Homebrew 版本号，升级就失效，每次还得重新授权。
 *   把它包成 .app：TCC 按 .app 的 bundle 来记权限，授权一次永久有效；
 *   .app 在"完全磁盘访问"的选择框里也能直接选（python 那种裸可执行文件会被灰掉）。
 *
 * 关键点：
 *   - 必须 fork+wait 子进程跑 python，不能 exec 替换自己 ——
 *     exec 后进程映像变成 python，就脱离 .app bundle，TCC 关联丢失。
 *   - 项目路径从自身可执行文件位置往上推 4 层得出，所以这个 .c 永远不用改、
 *     不用重编译，.app 的 cdhash 稳定，FDA 授权永久有效。
 *
 *     <project>/FeishuWorklog.app/Contents/MacOS/FeishuWorklog
 *              └4─────────┴3───────┴2───────┴1
 */
#include <unistd.h>
#include <sys/wait.h>
#include <mach-o/dyld.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    char raw[4096];
    uint32_t sz = sizeof(raw);
    if (_NSGetExecutablePath(raw, &sz) != 0) {
        fprintf(stderr, "launcher: 无法获取自身路径\n");
        return 1;
    }

    char project[4096];
    if (realpath(raw, project) == NULL) {
        fprintf(stderr, "launcher: realpath 失败\n");
        return 1;
    }
    /* 上溯 4 层：FeishuWorklog → MacOS → Contents → FeishuWorklog.app → <project> */
    for (int i = 0; i < 4; i++) {
        char *slash = strrchr(project, '/');
        if (slash == NULL) {
            fprintf(stderr, "launcher: 路径层级不对，无法定位项目目录\n");
            return 1;
        }
        *slash = '\0';
    }

    if (chdir(project) != 0) {
        fprintf(stderr, "launcher: 无法 chdir 到 %s\n", project);
        return 1;
    }

    char py[4096];
    snprintf(py, sizeof(py), "%s/.venv/bin/python", project);

    char *argv[] = { py, "-m", "scripts.run_daily", "--catch-up", NULL };

    pid_t pid = fork();
    if (pid < 0) {
        fprintf(stderr, "launcher: fork 失败\n");
        return 1;
    }
    if (pid == 0) {
        execv(py, argv);
        fprintf(stderr, "launcher: 无法执行 %s\n", py);
        _exit(127);
    }

    int status = 0;
    waitpid(pid, &status, 0);
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
