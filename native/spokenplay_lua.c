/*
 * spokenplay: the Lua 5.1 face of the player, for Windower.
 *
 *   local player = require('spokenplay')
 *   player.play(path)        -> true, or nil and why it could not start
 *   player.stop()
 *   player.state()           -> 'idle' | 'playing' | 'done' | 'error', message
 *   player.set_volume(0..1)
 *   player.version()
 *   player.shutdown()        -> ends the worker thread; the addon calls it on unload
 *
 * None of these block: play hands the path to the worker thread and
 * returns. The Lua state is only ever touched on the thread that called in.
 *
 * Build with `make native` (mingw-w64, 32-bit, linked against Windower's
 * LuaCore.dll and static libogg/libopus/opusfile); the result goes to
 * libs/spokenplay.dll.
 */
#include <windows.h>

#include "lua.h"
#include "lauxlib.h"

#include "spokenplay.h"

static const char *STATE_NAMES[] = {"idle", "playing", "done", "error"};

static int l_play(lua_State *L)
{
    const char *path = luaL_checkstring(L, 1);
    char err[256];
    if (sp_play(path, err, sizeof err) != 0) {
        lua_pushnil(L);
        lua_pushstring(L, err);
        return 2;
    }
    lua_pushboolean(L, 1);
    return 1;
}

static int l_stop(lua_State *L)
{
    (void)L;
    sp_stop();
    return 0;
}

static int l_state(lua_State *L)
{
    char msg[256];
    enum sp_state s = sp_state(msg, sizeof msg);
    lua_pushstring(L, STATE_NAMES[s]);
    if (msg[0]) {
        lua_pushstring(L, msg);
    } else {
        lua_pushnil(L);
    }
    return 2;
}

static int l_set_volume(lua_State *L)
{
    sp_set_volume((float)luaL_checknumber(L, 1));
    return 0;
}

static int l_version(lua_State *L)
{
    lua_pushstring(L, sp_version());
    return 1;
}

static int l_shutdown(lua_State *L)
{
    (void)L;
    sp_shutdown();
    return 0;
}

/* The sentinel's finaliser: when the addon's Lua state closes (//lua
 * unload, //lua reload) without calling shutdown, this runs before loadlib
 * frees the DLL, so the worker is never left running in unmapped code. */
static int l_gc(lua_State *L)
{
    (void)L;
    sp_shutdown();
    return 0;
}

static const luaL_Reg functions[] = {
    {"play", l_play},
    {"stop", l_stop},
    {"state", l_state},
    {"set_volume", l_set_volume},
    {"version", l_version},
    {"shutdown", l_shutdown},
    {NULL, NULL}
};

__declspec(dllexport) int luaopen_spokenplay(lua_State *L)
{
    luaL_register(L, "spokenplay", functions);

    if (luaL_newmetatable(L, "spokenplay.sentinel")) {
        lua_pushcfunction(L, l_gc);
        lua_setfield(L, -2, "__gc");
    }
    lua_pop(L, 1);
    lua_newuserdata(L, 1);
    luaL_getmetatable(L, "spokenplay.sentinel");
    lua_setmetatable(L, -2);
    lua_setfield(L, LUA_REGISTRYINDEX, "spokenplay.sentinel");

    return 1;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    (void)instance;
    (void)reserved;
    if (reason == DLL_PROCESS_DETACH) {
        /* last resort if nothing called shutdown: tell the worker to quit;
         * joining here would deadlock on the loader lock */
        sp_abandon();
    }
    return TRUE;
}
