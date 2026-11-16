--[[
Spoken: reads NPC dialogue aloud.

Work in progress. Inspired by the Spoken addon for World of Warcraft
(https://github.com/rusty-key/spoken-wow). The addon side only captures
dialogue and talks to a helper process through files in data/; see
docs/Protocol.md. The helper does the speaking.
]]

_addon.name = 'Spoken'
_addon.author = 'delphyne'
_addon.version = '0.0.2'
_addon.command = 'spoken'

require('luau')

local defaults = require('defaults')
local text = require('text')

local MODE = {
    MESSAGE = 150,        -- NPC dialogue that waits for the player
    SYSTEM = 151,         -- narration
    TIMED_MESSAGE = 144,  -- NPC dialogue that does not wait
}

local ZONE_OUT_PACKET = 0x0B
local LEAVE_CONVERSATION_PACKET = 0x52

local QUEUE_FILE = 'data/queue.jsonl'
local STATUS_FILE = 'data/status.json'

local settings = config.load(defaults)

local state = {
    seq = 0,
    current = nil,          -- the last speak entry written
    advanced_seq = nil,     -- seq we already auto-advanced for
    frame = 0,
    status = nil,           -- last parsed status.json
    debug = false,
}

-------------------------------------------------------------------------------
-- queue file

local function json_string(s)
    s = s:gsub('[%c"\\]', function(c)
        if c == '"' then return '\\"' end
        if c == '\\' then return '\\\\' end
        if c == '\n' then return '\\n' end
        return ('\\u%04x'):format(c:byte())
    end)
    return '"' .. s .. '"'
end

local function append_queue(fields)
    local f = io.open(windower.addon_path .. QUEUE_FILE, 'a')
    if not f then
        if state.debug then print('Spoken: cannot open ' .. QUEUE_FILE) end
        return false
    end
    f:write(fields, '\n')
    f:close()
    return true
end

local function write_speak(speaker, line, prompted, mode)
    state.seq = state.seq + 1
    local entry = ('{"seq": %d, "speaker": %s, "text": %s, "prompted": %s, "mode": %d, "t": %d}'):format(
        state.seq, json_string(speaker), json_string(line), prompted and 'true' or 'false', mode, os.time())
    state.current = {seq = state.seq, speaker = speaker, text = line, prompted = prompted}
    state.advanced_seq = nil
    if state.debug then print('Spoken: ' .. entry) end
    return append_queue(entry)
end

local function write_stop(reason)
    if not state.current then return end
    state.seq = state.seq + 1
    state.current = nil
    append_queue(('{"seq": %d, "stop": true, "reason": %s}'):format(state.seq, json_string(reason)))
end

-- status.json is tiny and flat, so a few patterns are enough
local function read_status()
    local f = io.open(windower.addon_path .. STATUS_FILE, 'r')
    if not f then return nil end
    local content = f:read('*a')
    f:close()
    if not content or content == '' then return nil end
    return {
        seq = tonumber(content:match('"seq"%s*:%s*(%d+)')),
        state = content:match('"state"%s*:%s*"([^"]*)"'),
        message = content:match('"message"%s*:%s*"([^"]*)"'),
    }
end

-- The player's name as the game prints it in dialogue; nil when not logged in.
local function player_name()
    local player = windower.ffxi and windower.ffxi.get_player and windower.ffxi.get_player()
    return player and player.name or nil
end

-------------------------------------------------------------------------------
-- events

windower.register_event('incoming text', function(original, modified, mode)
    if not settings.Enabled then return end
    if mode == MODE.SYSTEM and not settings.System then return end
    if not S{MODE.MESSAGE, MODE.TIMED_MESSAGE, MODE.SYSTEM}[mode] then return end

    -- a blank self-continuing prompt is a scene change, not speech
    if text.is_auto_prompt(original) then
        write_stop('advance')
        return
    end

    local speaker, line, prompted = text.clean(original)
    if line == '' then return end
    speaker, line = text.player_token(speaker, line, player_name())

    write_speak(speaker, line, prompted, mode)
end)

windower.register_event('incoming chunk', function(id)
    if id == LEAVE_CONVERSATION_PACKET then
        write_stop('release')
    elseif id == ZONE_OUT_PACKET then
        write_stop('zone')
    end
end)

function advance()
    -- Windower's setkey injects a key press; unverified under Wine
    windower.send_command('setkey enter down; wait 0.1; setkey enter up')
end

windower.register_event('prerender', function()
    state.frame = state.frame + 1
    if state.frame % settings.PollFrames ~= 0 then return end
    if not state.current then return end

    local status = read_status()
    state.status = status
    if not status or status.seq ~= state.current.seq then return end

    if status.state == 'done' and settings.AutoAdvance
        and state.current.prompted and state.advanced_seq ~= status.seq then
        state.advanced_seq = status.seq
        if state.debug then print('Spoken: auto-advancing after seq ' .. status.seq) end
        advance()
    end
end)

-------------------------------------------------------------------------------
-- commands

local function toggle(key, label)
    settings[key] = not settings[key]
    config.save(settings)
    log(label .. ' ' .. (settings[key] and 'on' or 'off'))
end

windower.register_event('addon command', function(command, ...)
    local args = L{...}
    command = command and command:lower() or 'help'

    if command == 'help' then
        log('Spoken v' .. _addon.version .. ' (work in progress)')
        log('  //spoken on|off      speak NPC dialogue')
        log('  //spoken system      also speak system narration')
        log('  //spoken auto        advance prompted lines when speech finishes')
        log('  //spoken stop        stop the current line')
        log('  //spoken status      what the helper last reported')
        log('  //spoken test <name> : <text>   queue a line without an NPC')
        log('  //spoken debug       print queue entries to the console')

    elseif command == 'on' or command == 'off' then
        settings.Enabled = command == 'on'
        config.save(settings)
        log('speaking ' .. (settings.Enabled and 'on' or 'off'))

    elseif command == 'system' then
        toggle('System', 'system narration')

    elseif command == 'auto' then
        toggle('AutoAdvance', 'auto-advance')

    elseif command == 'stop' then
        write_stop('command')
        log('stopped')

    elseif command == 'status' then
        local status = read_status()
        if not status then
            log('no status from the helper yet (is it running? make helper)')
        else
            log(('helper: seq %s %s%s'):format(tostring(status.seq), tostring(status.state),
                status.message and (' (' .. status.message .. ')') or ''))
        end
        if state.current then
            log(('current: seq %d %s: %s'):format(state.current.seq, state.current.speaker, state.current.text))
        end

    elseif command == 'test' then
        local raw = args:concat(' ')
        local speaker, line, prompted = text.clean(raw)
        write_speak(speaker, line, prompted, MODE.MESSAGE)
        log(('queued seq %d'):format(state.seq))

    elseif command == 'debug' then
        state.debug = not state.debug
        log('debug ' .. (state.debug and 'on' or 'off'))

    else
        log('unknown command; //spoken help')
    end
end)
