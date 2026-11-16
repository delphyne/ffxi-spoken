/*
 * spokenplay core: one worker thread, a mailbox, and a generation counter.
 *
 * The game thread only ever locks the mailbox for a few instructions: play
 * drops a path in it and bumps the generation, stop bumps the generation,
 * state reads two fields. The worker reads the file, decodes it with
 * opusfile in 100 ms pieces, and feeds four waveOut buffers; whenever it
 * wakes it compares its generation with the current one and tears down if
 * a newer request arrived. A terminal state (done, error) is only written
 * when the generation still matches, so a stale result never overwrites a
 * newer line.
 *
 * waveOut rather than DirectSound or XAudio2: plain C, no COM, no window
 * handle, and under Wine it goes through the same shared-mode mixer the
 * game's own sound uses. Always 48 kHz stereo 16-bit, which every mixer
 * format accepts; opusfile upmixes mono for us.
 */
#include <windows.h>
#include <mmsystem.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <opusfile.h>

#include "spokenplay.h"

#define RATE 48000
#define CHANNELS 2
#define BUFFERS 4
#define BUFFER_MS 100
#define BUFFER_VALUES (RATE * BUFFER_MS / 1000 * CHANNELS)   /* int16 values per buffer */
#define SCRATCH_VALUES (5760 * CHANNELS)                       /* op_read_stereo's largest return */
#define MAX_FILE_BYTES (64u << 20)
#define GAIN_ONE 32768

static struct {
    int initialised;
    CRITICAL_SECTION lock;
    HANDLE thread;
    HANDLE req_event;        /* auto-reset: the mailbox changed, or quit */
    HANDLE wave_event;       /* auto-reset: waveOut returned a buffer */
    volatile LONG gen;
    volatile LONG state;
    volatile LONG gain_q15;
    volatile LONG quit;
    /* mailbox, under lock */
    int req_pending;
    LONG req_gen;
    WCHAR *req_path;
    char err[256];
} P;

/* ------------------------------------------------------------------------ */
/* worker                                                                   */

static int superseded(LONG my_gen)
{
    return P.gen != my_gen || P.quit;
}

static void finish(LONG my_gen, LONG state, const char *fmt, ...)
{
    char msg[256] = "";
    if (fmt) {
        va_list ap;
        va_start(ap, fmt);
        _vsnprintf(msg, sizeof msg - 1, fmt, ap);
        va_end(ap);
    }
    EnterCriticalSection(&P.lock);
    if (P.gen == my_gen) {
        P.state = state;
        strcpy(P.err, msg);
    }
    LeaveCriticalSection(&P.lock);
}

static void *read_file(const WCHAR *path, DWORD *size, char *err, size_t errlen)
{
    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    LARGE_INTEGER len;
    void *bytes;
    DWORD got = 0;

    if (h == INVALID_HANDLE_VALUE) {
        _snprintf(err, errlen, "cannot open file (%lu)", GetLastError());
        return NULL;
    }
    if (!GetFileSizeEx(h, &len) || len.QuadPart <= 0 || len.QuadPart > MAX_FILE_BYTES) {
        _snprintf(err, errlen, "file is empty or too large");
        CloseHandle(h);
        return NULL;
    }
    bytes = malloc((size_t)len.QuadPart);
    if (!bytes || !ReadFile(h, bytes, (DWORD)len.QuadPart, &got, NULL) || got != (DWORD)len.QuadPart) {
        _snprintf(err, errlen, "cannot read file (%lu)", GetLastError());
        free(bytes);
        CloseHandle(h);
        return NULL;
    }
    CloseHandle(h);
    *size = got;
    return bytes;
}

struct decoder {
    OggOpusFile *of;
    opus_int16 scratch[SCRATCH_VALUES];
    int scratch_len, scratch_pos;   /* values */
    int eof;
    int error;                      /* an opusfile error code, or 0 */
};

/* Fills dst with up to max values of gained stereo PCM; 0 at the end. */
static int fill(struct decoder *d, opus_int16 *dst, int max)
{
    int n = 0;
    LONG gain = P.gain_q15;

    while (n < max && !d->eof && !d->error) {
        if (d->scratch_pos >= d->scratch_len) {
            int got = op_read_stereo(d->of, d->scratch, SCRATCH_VALUES);
            if (got == OP_HOLE) continue;          /* a damaged page: skip it */
            if (got < 0) { d->error = got; break; }
            if (got == 0) { d->eof = 1; break; }
            d->scratch_len = got * CHANNELS;
            d->scratch_pos = 0;
        }
        {
            int take = d->scratch_len - d->scratch_pos;
            int i;
            if (take > max - n) take = max - n;
            if (gain == GAIN_ONE) {
                memcpy(dst + n, d->scratch + d->scratch_pos, (size_t)take * sizeof(opus_int16));
            } else {
                for (i = 0; i < take; i++) {
                    LONG v = ((LONG)d->scratch[d->scratch_pos + i] * gain) >> 15;
                    dst[n + i] = (opus_int16)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
                }
            }
            n += take;
            d->scratch_pos += take;
        }
    }
    return n;
}

static void play_file(const WCHAR *path, LONG my_gen)
{
    char err[256];
    DWORD size = 0;
    void *bytes;
    struct decoder d;
    int oerr = 0;
    WAVEFORMATEX fmt;
    HWAVEOUT hwo = NULL;
    WAVEHDR hdr[BUFFERS];
    int queued[BUFFERS];
    opus_int16 *pcm;
    MMRESULT r;
    HANDLE events[2];
    int i, done_state = SPS_DONE;
    char done_msg[256] = "";

    bytes = read_file(path, &size, err, sizeof err);
    if (!bytes) { finish(my_gen, SPS_ERROR, "%s", err); return; }

    memset(&d, 0, sizeof d);
    d.of = op_open_memory(bytes, size, &oerr);
    if (!d.of) {
        free(bytes);
        finish(my_gen, SPS_ERROR, "not an Opus file (%d)", oerr);
        return;
    }

    memset(&fmt, 0, sizeof fmt);
    fmt.wFormatTag = WAVE_FORMAT_PCM;
    fmt.nChannels = CHANNELS;
    fmt.nSamplesPerSec = RATE;
    fmt.wBitsPerSample = 16;
    fmt.nBlockAlign = (WORD)(CHANNELS * 2);
    fmt.nAvgBytesPerSec = RATE * fmt.nBlockAlign;

    r = waveOutOpen(&hwo, WAVE_MAPPER, &fmt, (DWORD_PTR)P.wave_event, 0, CALLBACK_EVENT);
    if (r != MMSYSERR_NOERROR) {
        op_free(d.of);
        free(bytes);
        finish(my_gen, SPS_ERROR, "waveOutOpen failed (%u)", (unsigned)r);
        return;
    }

    pcm = (opus_int16 *)malloc((size_t)BUFFERS * BUFFER_VALUES * sizeof(opus_int16));
    memset(hdr, 0, sizeof hdr);
    for (i = 0; i < BUFFERS; i++) {
        hdr[i].lpData = (LPSTR)(pcm + (size_t)i * BUFFER_VALUES);
        hdr[i].dwBufferLength = BUFFER_VALUES * sizeof(opus_int16);
        waveOutPrepareHeader(hwo, &hdr[i], sizeof hdr[i]);
        queued[i] = 0;
    }

    events[0] = P.req_event;
    events[1] = P.wave_event;

    for (;;) {
        int busy = 0;

        if (superseded(my_gen)) { done_state = -1; break; }

        for (i = 0; i < BUFFERS; i++) {
            if (queued[i] && (hdr[i].dwFlags & WHDR_DONE)) queued[i] = 0;
            if (!queued[i] && !d.eof && !d.error) {
                int n = fill(&d, (opus_int16 *)hdr[i].lpData, BUFFER_VALUES);
                if (n > 0) {
                    hdr[i].dwBufferLength = (DWORD)n * sizeof(opus_int16);
                    hdr[i].dwFlags &= ~WHDR_DONE;
                    if (waveOutWrite(hwo, &hdr[i], sizeof hdr[i]) == MMSYSERR_NOERROR) {
                        queued[i] = 1;
                    } else {
                        d.error = 1;
                        strcpy(done_msg, "waveOutWrite failed");
                    }
                }
            }
            if (queued[i]) busy = 1;
        }

        if ((d.eof || d.error) && !busy) {
            if (d.error) {
                done_state = SPS_ERROR;
                if (!done_msg[0]) _snprintf(done_msg, sizeof done_msg - 1, "decode error (%d)", d.error);
            }
            break;
        }

        WaitForMultipleObjects(2, events, FALSE, BUFFER_MS);
    }

    waveOutReset(hwo);
    for (i = 0; i < BUFFERS; i++) waveOutUnprepareHeader(hwo, &hdr[i], sizeof hdr[i]);
    waveOutClose(hwo);
    op_free(d.of);
    free(bytes);
    free(pcm);

    if (done_state >= 0) finish(my_gen, done_state, done_msg[0] ? "%s" : NULL, done_msg);
}

static DWORD WINAPI worker(LPVOID arg)
{
    (void)arg;
    for (;;) {
        WCHAR *path = NULL;
        LONG my_gen = 0;

        if (P.quit) return 0;
        EnterCriticalSection(&P.lock);
        if (P.req_pending) {
            P.req_pending = 0;
            path = P.req_path;
            P.req_path = NULL;
            my_gen = P.req_gen;
        }
        LeaveCriticalSection(&P.lock);

        if (!path) {
            WaitForSingleObject(P.req_event, INFINITE);
            continue;
        }
        play_file(path, my_gen);
        free(path);
    }
}

/* ------------------------------------------------------------------------ */
/* the calling thread                                                       */

static int ensure_init(char *err, size_t errlen)
{
    if (P.initialised) return 0;
    memset(&P, 0, sizeof P);
    InitializeCriticalSection(&P.lock);
    P.req_event = CreateEventW(NULL, FALSE, FALSE, NULL);
    P.wave_event = CreateEventW(NULL, FALSE, FALSE, NULL);
    P.gain_q15 = GAIN_ONE;
    P.state = SPS_IDLE;
    if (!P.req_event || !P.wave_event) {
        _snprintf(err, errlen, "cannot create events (%lu)", GetLastError());
        return -1;
    }
    P.initialised = 1;
    return 0;
}

static WCHAR *to_wide(const char *s)
{
    int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, -1, NULL, 0);
    UINT cp = CP_UTF8;
    WCHAR *w;
    if (n <= 0) {
        cp = CP_ACP;
        n = MultiByteToWideChar(cp, 0, s, -1, NULL, 0);
        if (n <= 0) return NULL;
    }
    w = (WCHAR *)malloc((size_t)n * sizeof(WCHAR));
    if (!w) return NULL;
    MultiByteToWideChar(cp, 0, s, -1, w, n);
    return w;
}

int sp_play(const char *path, char *err, size_t errlen)
{
    WCHAR *wpath;
    DWORD attrs;

    err[0] = 0;
    if (ensure_init(err, errlen) != 0) return -1;

    wpath = to_wide(path);
    if (!wpath) {
        _snprintf(err, errlen, "bad path encoding");
        return -1;
    }
    attrs = GetFileAttributesW(wpath);
    if (attrs == INVALID_FILE_ATTRIBUTES || (attrs & FILE_ATTRIBUTE_DIRECTORY)) {
        free(wpath);
        _snprintf(err, errlen, "not found");
        return -1;
    }

    if (!P.thread) {
        P.quit = 0;
        P.thread = CreateThread(NULL, 0, worker, NULL, 0, NULL);
        if (!P.thread) {
            free(wpath);
            _snprintf(err, errlen, "cannot start the player thread (%lu)", GetLastError());
            return -1;
        }
    }

    EnterCriticalSection(&P.lock);
    P.gen++;
    free(P.req_path);
    P.req_path = wpath;
    P.req_gen = P.gen;
    P.req_pending = 1;
    P.state = SPS_PLAYING;
    P.err[0] = 0;
    LeaveCriticalSection(&P.lock);
    SetEvent(P.req_event);
    return 0;
}

void sp_stop(void)
{
    if (!P.initialised) return;
    EnterCriticalSection(&P.lock);
    P.gen++;
    P.req_pending = 0;
    free(P.req_path);
    P.req_path = NULL;
    P.state = SPS_IDLE;
    P.err[0] = 0;
    LeaveCriticalSection(&P.lock);
    SetEvent(P.req_event);
}

enum sp_state sp_state(char *msg, size_t msglen)
{
    LONG s;
    if (msglen) msg[0] = 0;
    if (!P.initialised) return SPS_IDLE;
    EnterCriticalSection(&P.lock);
    s = P.state;
    if (msglen) {
        strncpy(msg, P.err, msglen - 1);
        msg[msglen - 1] = 0;
    }
    LeaveCriticalSection(&P.lock);
    return (enum sp_state)s;
}

void sp_set_volume(float gain)
{
    LONG q;
    if (!(gain > 0.0f)) gain = 0.0f;
    if (gain > 1.0f) gain = 1.0f;
    q = (LONG)(gain * GAIN_ONE + 0.5f);
    if (P.initialised) {
        InterlockedExchange(&P.gain_q15, q);
    } else {
        char err[64];
        if (ensure_init(err, sizeof err) == 0) P.gain_q15 = q;
    }
}

void sp_abandon(void)
{
    if (!P.initialised) return;
    P.quit = 1;
    InterlockedIncrement(&P.gen);
    SetEvent(P.req_event);
}

void sp_shutdown(void)
{
    if (!P.initialised) return;
    sp_abandon();
    if (P.thread) {
        if (WaitForSingleObject(P.thread, 3000) != WAIT_OBJECT_0) {
            /* the worker is stuck in the audio driver: leave everything to
             * the process rather than free memory it may still touch */
            return;
        }
        CloseHandle(P.thread);
        P.thread = NULL;
    }
    CloseHandle(P.req_event);
    CloseHandle(P.wave_event);
    DeleteCriticalSection(&P.lock);
    free(P.req_path);
    memset(&P, 0, sizeof P);
}

const char *sp_version(void)
{
    return "spokenplay " SP_VERSION " (winmm, opusfile)";
}
