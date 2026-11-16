/*
 * spokenplay: plays one Ogg Opus file at a time through winmm, from a worker
 * thread, so a Lua addon can start, cut off, and watch a line of dialogue
 * without ever blocking the game. No Lua in here; spokenplay_lua.c binds it
 * and spokenplay_test.c drives it from a console.
 */
#ifndef SPOKENPLAY_H
#define SPOKENPLAY_H

#include <stddef.h>

#define SP_VERSION "0.1"

enum sp_state {
    SPS_IDLE,     /* nothing playing, nothing to report */
    SPS_PLAYING,  /* a play was requested and has not finished */
    SPS_DONE,     /* the last play reached the end of the file */
    SPS_ERROR     /* the last play failed; sp_state's message says why */
};

/* Stops whatever is playing and starts path (UTF-8, or the ANSI code page
 * when it is not valid UTF-8). Returns 0, or -1 with a message in err when
 * the file is not there or the player cannot start. Decoding and device
 * errors surface later through sp_state. */
int sp_play(const char *path, char *err, size_t errlen);

/* Cuts the current line. Harmless when nothing is playing. */
void sp_stop(void);

/* The state of the last request. msg receives the error message, or "". */
enum sp_state sp_state(char *msg, size_t msglen);

/* Software gain, 0 to 1, applied to everything queued from now on. */
void sp_set_volume(float gain);

/* Stops, ends the worker thread, and releases everything. Call before the
 * module is unloaded; sp_play after this starts over. */
void sp_shutdown(void);

/* Like sp_shutdown but never waits: for DllMain, where joining is unsafe. */
void sp_abandon(void);

const char *sp_version(void);

#endif
