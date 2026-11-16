--[[
Spoken: reads NPC dialogue aloud.

Work in progress. Inspired by the Spoken addon for World of Warcraft
(https://github.com/rusty-key/spoken-wow). The addon captures dialogue and,
in audio mode, plays the matching line from the installed audio packs under
audio/ through the native player (libs/spokenplay.dll); a line no pack has is
silent. In helper mode it writes the line to data/ for the developer's
helper process instead (docs/Protocol.md).
]]

_addon.name = 'Spoken'
_addon.author = 'delphyne'
_addon.version = '0.1.0'
_addon.command = 'spoken'

require('luau')

local defaults = require('defaults')
local text = require('text')
local packs = require('packs')

local MODE = {
    MESSAGE = 150,        -- NPC dialogue that waits for the player
    SYSTEM = 151,         -- narration
    TIMED_MESSAGE = 144,  -- NPC dialogue that does not wait
}

local ZONE_OUT_PACKET = 0x0B
local LEAVE_CONVERSATION_PACKET = 0x52

local QUEUE_FILE = 'data/queue.jsonl'
local STATUS_FILE = 'data/status.json'
local MISS_FILE = 'data/misses.jsonl'
local AUDIO_DIR = 'audio'

local settings = config.load(defaults)

local state = {
    seq = 0,
    current = nil,          -- the line on screen: seq, speaker, text, prompted, mode, path or miss
    advanced_seq = nil,     -- seq we already auto-advanced for
    error_seq = nil,        -- seq whose player error was already printed
    frame = 0,
    status = nil,           -- last status seen: seq, state, message
    debug = false,
    index = nil,            -- the pack index, packs.load
}

-------------------------------------------------------------------------------
-- the native player, optional: without the DLL audio mode logs every line as
-- a miss and helper mode still works

local player, player_error
do
    -- package.cpath doesn't like backslashes
    local addon_path = windower.addon_path:gsub('\\', '/')
    package.cpath = package.cpath .. ';' .. addon_path .. 'libs/?.dll'
    local ok, result = pcall(require, 'spokenplay')
    if ok then
        player = result
    else
        player_error = tostring(result):match('^[^\n]*')
    end
end

local function apply_volume()
    if player then player.set_volume(math.max(0, math.min(100, settings.Volume)) / 100) end
end

-------------------------------------------------------------------------------
-- files

local fs = {
    get_dir = function(path) return windower.get_dir(path) end,
    dir_exists = function(path) return windower.dir_exists(path) end,
    file_exists = function(path) return windower.file_exists(path) end,
    read = function(path)
        local f = io.open(path, 'rb')
        if not f then return nil end
        local content = f:read('*a')
        f:close()
        return content
    end,
}

local function append_line(file, line)
    local f = io.open(windower.addon_path .. file, 'a')
    if not f then
        if state.debug then print('Spoken: cannot open ' .. file) end
        return false
    end
    f:write(line, '\n')
    f:close()
    return true
end

local function load_packs()
    state.index = packs.load(windower.addon_path .. AUDIO_DIR, fs)
    return state.index
end

-------------------------------------------------------------------------------
-- JSON out: the queue entries and the miss log

local function json_string(s)
    s = s:gsub('[%c"\\]', function(c)
        if c == '"' then return '\\"' end
        if c == '\\' then return '\\\\' end
        if c == '\n' then return '\\n' end
        return ('\\u%04x'):format(c:byte())
    end)
    return '"' .. s .. '"'
end

-- every byte outside printable ASCII escaped, so raw Shift-JIS survives
local function json_bytes(s)
    s = s:gsub('["\\]', '\\%0'):gsub('[^\32-\126]', function(c) return ('\\u%04x'):format(c:byte()) end)
    return '"' .. s .. '"'
end

local OBJECT = {}
-- an ordered object: {{key, value}, ...}
local function object(fields) return setmetatable(fields, OBJECT) end

local function encode(v)
    local t = type(v)
    if t == 'string' then return json_string(v) end
    if t == 'number' then return v % 1 == 0 and ('%d'):format(v) or ('%.3f'):format(v) end
    if t == 'boolean' then return tostring(v) end
    if t ~= 'table' then return 'null' end
    if v.json then return v.json end
    local parts = {}
    if getmetatable(v) == OBJECT then
        for _, field in ipairs(v) do parts[#parts + 1] = json_string(field[1]) .. ': ' .. encode(field[2]) end
        return '{' .. table.concat(parts, ', ') .. '}'
    end
    for _, item in ipairs(v) do parts[#parts + 1] = encode(item) end
    return '[' .. table.concat(parts, ', ') .. ']'
end

-------------------------------------------------------------------------------
-- helper mode: the queue file (docs/Protocol.md)

local function write_speak(speaker, line, prompted, mode)
    local entry = ('{"seq": %d, "speaker": %s, "text": %s, "prompted": %s, "mode": %d, "t": %d}'):format(
        state.seq, json_string(speaker), json_string(line), prompted and 'true' or 'false', mode, os.time())
    if state.debug then print('Spoken: ' .. entry) end
    return append_line(QUEUE_FILE, entry)
end

local function write_stop(reason)
    state.seq = state.seq + 1
    append_line(QUEUE_FILE, ('{"seq": %d, "stop": true, "reason": %s}'):format(state.seq, json_string(reason)))
end

-- status.json is tiny and flat, so a few patterns are enough
local function read_status()
    local content = fs.read(windower.addon_path .. STATUS_FILE)
    if not content or content == '' then return nil end
    return {
        seq = tonumber(content:match('"seq"%s*:%s*(%d+)')),
        state = content:match('"state"%s*:%s*"([^"]*)"'),
        message = content:match('"message"%s*:%s*"([^"]*)"'),
    }
end

-------------------------------------------------------------------------------
-- where we are, for the miss log; nil for anything the game does not answer

local ffxi = windower.ffxi or {}

local function player_info()
    return ffxi.get_player and ffxi.get_player() or nil
end

-- The player's name as the game prints it in dialogue; nil when not logged in.
local function player_name()
    local me = player_info()
    return me and me.name or nil
end

local function whereabouts()
    local info = ffxi.get_info and ffxi.get_info()
    local me = player_info()
    local mob = me and me.index and ffxi.get_mob_by_index and ffxi.get_mob_by_index(me.index)
    local target = ffxi.get_mob_by_target and ffxi.get_mob_by_target('t')
    return info and info.zone,
        mob and object{{'x', mob.x}, {'y', mob.y}, {'z', mob.z}},
        target and object{{'name', target.name}, {'id', target.id}, {'index', target.index}}
end

-------------------------------------------------------------------------------
-- audio mode

-- One row per line no pack could speak, with everything needed to work out
-- why: the raw bytes, where the player stood, who they were talking to, and
-- the nearest lines the packs do have.
local function log_miss(reason, detail, speaker, line, prompted, mode, original)
    if state.debug then
        print(('Spoken: missed (%s) %s: %s'):format(reason, speaker, line))
    end
    if not (settings.Log or state.debug) then return end
    local zone, pos, target = whereabouts()
    local diagnosis = packs.diagnose(state.index, speaker, line)
    local near = {}
    for _, row in ipairs(diagnosis.near) do
        near[#near + 1] = object{{'speaker', row.speaker}, {'text', row.text}}
    end
    local row = object{
        {'t', os.time()},
        {'reason', reason},
        {'detail', detail},
        {'speaker', speaker},
        {'text', line},
        {'prompted', prompted},
        {'mode', mode},
        {'raw', original and {json = json_bytes(original)} or nil},
        {'player', player_name()},
        {'zone', zone},
        {'pos', pos},
        {'target', target},
        {'speakers_with_text', diagnosis.speakers_with_text},
        {'near', near},
        {'packs', packs.describe(state.index)},
    }
    append_line(MISS_FILE, encode(row))
end

-- Finds and plays the line; false with the reason when it cannot.
local function play(speaker, line, prompted, mode, original)
    local entry = packs.lookup(state.index, speaker, line)
    local reason, detail
    if not entry then
        reason = 'unknown'
    elseif not fs.file_exists(entry.path) then
        reason, detail = 'file_missing', entry.path
    elseif not player then
        reason, detail = 'no_player', player_error
    else
        local ok, err = player.play(entry.path)
        if not ok then reason, detail = 'play_failed', err end
    end
    if reason then
        -- the new line is on screen, so whatever was playing is stale
        if player then player.stop() end
        state.current.miss = reason
        log_miss(reason, detail, speaker, line, prompted, mode, original)
        return false, reason, detail
    end
    state.current.path = entry.path
    if state.debug then print('Spoken: playing ' .. entry.path) end
    return true
end

-------------------------------------------------------------------------------
-- the two seams between capture and whichever side speaks

local function speak(speaker, line, prompted, mode, original)
    state.seq = state.seq + 1
    state.current = {seq = state.seq, speaker = speaker, text = line, prompted = prompted, mode = mode}
    state.advanced_seq = nil
    if settings.Mode == 'helper' then
        return write_speak(speaker, line, prompted, mode)
    end
    return play(speaker, line, prompted, mode, original)
end

local function stop(reason)
    if not state.current then return end
    state.current = nil
    if settings.Mode == 'helper' then
        write_stop(reason)
    elseif player then
        player.stop()
    end
end

-------------------------------------------------------------------------------
-- events

windower.register_event('incoming text', function(original, modified, mode)
    if not settings.Enabled then return end
    if mode == MODE.SYSTEM and not settings.System then return end
    if not S{MODE.MESSAGE, MODE.TIMED_MESSAGE, MODE.SYSTEM}[mode] then return end

    -- a blank self-continuing prompt is a scene change, not speech
    if text.is_auto_prompt(original) then
        stop('advance')
        return
    end

    local speaker, line, prompted = text.clean(original)
    if line == '' then return end
    speaker, line = text.player_token(speaker, line, player_name())

    speak(speaker, line, prompted, mode, original)
end)

windower.register_event('incoming chunk', function(id)
    if id == LEAVE_CONVERSATION_PACKET then
        stop('release')
    elseif id == ZONE_OUT_PACKET then
        stop('zone')
    end
end)

function advance()
    -- Windower's setkey injects a key press; unverified under Wine
    windower.send_command('setkey enter down; wait 0.1; setkey enter up')
end

-- What the speaking side says about the current line: the helper's status
-- file, or the player's state. nil until it is about this line.
local function poll()
    if settings.Mode == 'helper' then
        local status = read_status()
        if not status or status.seq ~= state.current.seq then return nil end
        return status
    end
    if not player or not state.current.path then return nil end
    local st, message = player.state()
    return {seq = state.current.seq, state = st, message = message}
end

windower.register_event('prerender', function()
    state.frame = state.frame + 1
    if state.frame % settings.PollFrames ~= 0 then return end
    if not state.current then return end

    local status = poll()
    if not status then return end
    state.status = status

    if status.state == 'error' and state.debug and state.error_seq ~= status.seq then
        state.error_seq = status.seq
        print(('Spoken: seq %d failed: %s'):format(status.seq, tostring(status.message)))
    end

    if status.state == 'done' and settings.AutoAdvance
        and state.current.prompted and state.advanced_seq ~= status.seq then
        state.advanced_seq = status.seq
        if state.debug then print('Spoken: auto-advancing after seq ' .. status.seq) end
        advance()
    end
end)

windower.register_event('unload', function()
    if player then
        player.stop()
        if player.shutdown then player.shutdown() end
    end
end)

-------------------------------------------------------------------------------
-- commands

local function toggle(key, label)
    settings[key] = not settings[key]
    config.save(settings)
    log(label .. ' ' .. (settings[key] and 'on' or 'off'))
end

local function describe_player()
    if not player then return 'player not loaded: ' .. tostring(player_error) end
    return 'player ' .. (player.version and player.version() or 'loaded')
end

local function report_packs()
    for _, line in ipairs(packs.describe(state.index)) do log(line) end
    for _, warning in ipairs(state.index.warnings) do log('  ' .. warning) end
    if state.index.conflicts > 0 then
        log(('  %d lines appear in more than one pack; the first pack by name wins'):format(state.index.conflicts))
    end
end

windower.register_event('addon command', function(command, ...)
    local args = L{...}
    command = command and command:lower() or 'help'

    if command == 'help' then
        log('Spoken v' .. _addon.version .. ' (work in progress)')
        log('  //spoken on|off      speak NPC dialogue')
        log('  //spoken mode [audio|helper]   play installed packs, or queue lines for the helper')
        log('  //spoken system      also speak system narration')
        log('  //spoken auto        advance prompted lines when speech finishes')
        log('  //spoken volume <0-100>')
        log('  //spoken stop        stop the current line')
        log('  //spoken status      mode, player, packs, and the current line')
        log('  //spoken packs       the installed audio packs')
        log('  //spoken reload      rescan audio/ after installing a pack')
        log('  //spoken log         write lines no pack has to data/misses.jsonl')
        log('  //spoken test <name> : <text>   speak a line without an NPC')
        log('  //spoken debug       print what happens to the console')

    elseif command == 'on' or command == 'off' then
        settings.Enabled = command == 'on'
        config.save(settings)
        log('speaking ' .. (settings.Enabled and 'on' or 'off'))

    elseif command == 'mode' then
        local mode = args[1] and args[1]:lower()
        if mode == 'audio' or mode == 'helper' then
            stop('mode')
            settings.Mode = mode
            config.save(settings)
        elseif mode then
            log('modes are audio and helper')
        end
        log('mode ' .. settings.Mode .. (settings.Mode == 'audio' and (', ' .. describe_player()) or ''))

    elseif command == 'system' then
        toggle('System', 'system narration')

    elseif command == 'auto' then
        toggle('AutoAdvance', 'auto-advance')

    elseif command == 'log' then
        toggle('Log', 'miss log (' .. MISS_FILE .. ')')

    elseif command == 'volume' then
        local volume = tonumber(args[1])
        if volume then
            settings.Volume = math.max(0, math.min(100, volume))
            config.save(settings)
            apply_volume()
        end
        log(('volume %d'):format(settings.Volume))

    elseif command == 'stop' then
        stop('command')
        log('stopped')

    elseif command == 'packs' then
        report_packs()

    elseif command == 'reload' then
        load_packs()
        report_packs()

    elseif command == 'status' then
        log('mode ' .. settings.Mode .. ', ' .. describe_player())
        report_packs()
        if settings.Mode == 'helper' then
            local status = read_status()
            if not status then
                log('no status from the helper yet (is it running? make helper)')
            else
                log(('helper: seq %s %s%s'):format(tostring(status.seq), tostring(status.state),
                    status.message and (' (' .. status.message .. ')') or ''))
            end
        elseif player then
            local st, message = player.state()
            log('player: ' .. tostring(st) .. (message and (' (' .. message .. ')') or ''))
        end
        if state.current then
            log(('current: seq %d %s: %s'):format(state.current.seq, state.current.speaker, state.current.text))
            if state.current.path then log('  ' .. state.current.path) end
            if state.current.miss then log('  missed: ' .. state.current.miss) end
        end

    elseif command == 'test' then
        local raw = args:concat(' ')
        local speaker, line, prompted = text.clean(raw)
        speaker, line = text.player_token(speaker, line, player_name())
        local ok, reason, detail = speak(speaker, line, prompted, MODE.MESSAGE, raw)
        if settings.Mode == 'helper' then
            log(('queued seq %d'):format(state.seq))
        elseif ok then
            log('playing ' .. state.current.path)
        else
            log(('missed (%s%s) %s: %s'):format(reason, detail and (': ' .. detail) or '', speaker, line))
        end

    elseif command == 'debug' then
        state.debug = not state.debug
        log('debug ' .. (state.debug and 'on' or 'off'))

    else
        log('unknown command; //spoken help')
    end
end)

-------------------------------------------------------------------------------
-- start

load_packs()
apply_volume()
log(('mode %s, %s'):format(settings.Mode, describe_player()))
for _, line in ipairs(packs.describe(state.index)) do log(line) end
if #state.index.warnings > 0 then
    log(('%d pack warning(s); //spoken packs'):format(#state.index.warnings))
end
