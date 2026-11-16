/*
 * Drives the player from a console, outside the game:
 *
 *   spokenplay_test.exe <file.opus> [stop-after-ms] [volume-percent]
 *
 * Prints every state change with a timestamp, cuts the sound after the
 * given delay when one is passed, and exits 0 when the line finished or was
 * stopped, 2 on a player error. `make native-test FILE=...` runs it under
 * the game's Wine prefix.
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

#include "spokenplay.h"

static const char *NAMES[] = {"idle", "playing", "done", "error"};

int main(int argc, char **argv)
{
    char err[256], msg[256];
    DWORD t0, stop_after = 0;
    int stopped = 0;
    enum sp_state last = (enum sp_state)-1;

    if (argc < 2) {
        fprintf(stderr, "usage: %s <file.opus> [stop-after-ms] [volume-percent]\n", argv[0]);
        return 1;
    }
    if (argc > 2) stop_after = (DWORD)atoi(argv[2]);
    if (argc > 3) sp_set_volume((float)atoi(argv[3]) / 100.0f);

    printf("%s\n", sp_version());
    if (sp_play(argv[1], err, sizeof err) != 0) {
        fprintf(stderr, "play: %s\n", err);
        return 2;
    }
    t0 = GetTickCount();

    for (;;) {
        enum sp_state s = sp_state(msg, sizeof msg);
        if (s != last) {
            printf("%6lu ms  %s%s%s\n", (unsigned long)(GetTickCount() - t0), NAMES[s], msg[0] ? ": " : "", msg);
            fflush(stdout);
            last = s;
        }
        if (s != SPS_PLAYING) break;
        if (stop_after && !stopped && GetTickCount() - t0 >= stop_after) {
            sp_stop();
            stopped = 1;
            printf("%6lu ms  stop requested\n", (unsigned long)(GetTickCount() - t0));
        }
        Sleep(50);
    }

    sp_shutdown();
    return last == SPS_ERROR ? 2 : 0;
}
