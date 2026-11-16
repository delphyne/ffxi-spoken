-- Tests for Spoken's capture and playback pipeline, run outside the game:
--   luajit test/test_Spoken.lua        (from the addon directory)
-- Windower's globals and libs (luau, config, chat.chars) are stubbed, the
-- native player is a fake that records its calls, the file system
-- (windower.get_dir and friends, io.open) is an in-memory tree so the pack
-- index can be built and the queue and miss log read back.

package.path = './?.lua;' .. package.path

local chat = {}
local handlers = {}
local files = {}
local dirs = {}
local commands = {}

-------------------------------------------------------------------------------
-- luau stubs

S = function(t) local s = {} for _, v in ipairs(t) do s[v] = true end return s end
L = function(t)
    return setmetatable(t, {__index = {
        empty = function(self) return #self == 0 end,
        concat = function(self, sep) return table.concat(self, sep) end,
    }})
end
log = function(line) chat[#chat + 1] = line end

package.preload['luau'] = function() end
package.preload['config'] = function()
    return {load = function(defaults) return defaults end, save = function() end}
end
package.preload['chat.chars'] = function()
    return {
        note = string.char(0x81, 0xED), sharp = string.char(0x81, 0xEB), flat = string.char(0x81, 0xEC),
        bstar = string.char(0x81, 0x99), wstar = string.char(0x81, 0x9A), wave = string.char(0x81, 0x60),
    }
end
config = require('config')

-------------------------------------------------------------------------------
-- the native player, faked

local fake = {calls = {}, st = 'idle', msg = nil, fail = nil, volume = nil}
package.preload['spokenplay'] = function()
    return {
        play = function(path)
            fake.calls[#fake.calls + 1] = 'play ' .. path
            if fake.fail then return nil, fake.fail end
            fake.st = 'playing'
            return true
        end,
        stop = function() fake.calls[#fake.calls + 1] = 'stop'; fake.st = 'idle' end,
        state = function() return fake.st, fake.msg end,
        set_volume = function(v) fake.volume = v end,
        version = function() return 'fake 0' end,
        shutdown = function() fake.calls[#fake.calls + 1] = 'shutdown' end,
    }
end

local function last_call() return fake.calls[#fake.calls] end

-------------------------------------------------------------------------------
-- windower stubs

-- the addon builds Windows paths with backslashes; the tree uses slashes
local function norm(path) return (path:gsub('\\', '/')) end

_addon = {}
windower = {
    addon_path = './',
    register_event = function(name, fn) handlers[name] = fn end,
    add_to_chat = function(_, line) chat[#chat + 1] = line end,
    from_shift_jis = function(s) return s end,
    send_command = function(cmd) commands[#commands + 1] = cmd end,
    get_dir = function(path) return dirs[norm(path)] end,
    dir_exists = function(path) return dirs[norm(path)] ~= nil end,
    file_exists = function(path) return files[norm(path)] ~= nil end,
    ffxi = {
        get_player = function() return {name = 'Tester', index = 42} end,
        get_info = function() return {zone = 231} end,
        get_mob_by_index = function(i) return i == 42 and {x = 1.5, y = -2, z = 0} or nil end,
        get_mob_by_target = function(t) return t == 't' and {name = 'Iroha', id = 17000001, index = 123} or nil end,
    },
}

-- in-memory files: io.open returns handles over the `files` table for the
-- whole run, since the addon opens the queue on every line
io.open = function(path, mode)
    path = norm(path)
    mode = mode or 'r'
    if mode:find('r') and files[path] == nil then return nil end
    if mode:find('w') then files[path] = '' end
    if mode:find('a') and files[path] == nil then files[path] = '' end
    local pos = 0
    return {
        write = function(_, ...) for _, s in ipairs({...}) do files[path] = files[path] .. s end end,
        read = function(_, fmt) local s = files[path]:sub(pos + 1); pos = #files[path]; return s end,
        close = function() end,
    }
end

-------------------------------------------------------------------------------
-- two installed packs, an older version, a download in progress, and junk

local AUDIO = './audio'
dirs[AUDIO] = {'rhapsodies', 'core', 'empty', 'notes.txt'}
dirs[AUDIO .. '/rhapsodies'] = {'0.1', '0.0.9', '0.2.part'}
dirs[AUDIO .. '/rhapsodies/0.1'] = {'manifest.json', 'Iroha', 'Gilgamesh', 'Cait Sith', 'narration'}
dirs[AUDIO .. '/rhapsodies/0.0.9'] = {'manifest.json'}
dirs[AUDIO .. '/rhapsodies/0.2.part'] = {}
dirs[AUDIO .. '/core'] = {'0.4'}
dirs[AUDIO .. '/core/0.4'] = {'manifest.json'}
dirs[AUDIO .. '/empty'] = {}

files[AUDIO .. '/rhapsodies/0.1/manifest.json'] = [[
{
 "pack": "rhapsodies",
 "version": "0.1",
 "format": {"codec": "opus", "bitrate_kbps": 48, "channels": 1},
 "lines": [
  {"speaker": "Iroha", "text": "Welcome, traveler.", "file": "Iroha/welcome-traveler.aaaa.opus", "duration": 1.5,
   "sources": [{"zone": 231, "event": 12, "text_id": 7741, "game_text": "65eb83a74c4371fc"}]},
  {"speaker": "Iroha", "text": "Well met, <player>.", "file": "Iroha/well-met-player.bbbb.opus", "duration": 1.2},
  {"speaker": "Gilgamesh", "text": "Yar, \"Iroha\" said so.", "file": "Gilgamesh/yar-iroha-said-so.cccc.opus", "duration": 2},
  {"speaker": "Cait Sith", "text": "Café time.", "file": "Cait Sith/cafe-time.dddd.opus", "duration": 1},
  {"speaker": "", "text": "Narration.", "file": "narration/narration.eeee.opus", "duration": 1}
 ],
 "skipped": {"menu": 1}
}
]]
files[AUDIO .. '/rhapsodies/0.1/Iroha/welcome-traveler.aaaa.opus'] = 'opus'
files[AUDIO .. '/rhapsodies/0.1/Iroha/well-met-player.bbbb.opus'] = 'opus'
files[AUDIO .. '/rhapsodies/0.1/Cait Sith/cafe-time.dddd.opus'] = 'opus'
files[AUDIO .. '/rhapsodies/0.1/narration/narration.eeee.opus'] = 'opus'
-- Gilgamesh's file is deliberately absent

files[AUDIO .. '/rhapsodies/0.0.9/manifest.json'] =
    '{"lines": [{"speaker": "Iroha", "text": "Welcome, traveler.", "file": "Iroha/old.opus"}]}'

files[AUDIO .. '/core/0.4/manifest.json'] = [[
{"lines": [
  {"speaker": "Guard", "text": "Halt!", "file": "Guard/halt.ffff.opus"},
  {"speaker": "Iroha's Voice", "text": "Welcome, traveler.", "file": "Iroha's Voice/welcome.gggg.opus"},
  {"speaker": "", "text": "Narration.", "file": "narration/narration.hhhh.opus"}
]}
]]
files[AUDIO .. '/core/0.4/narration/narration.hhhh.opus'] = 'opus'

-------------------------------------------------------------------------------

local failures = 0
local function check(name, got, want)
    if got == want then
        print('ok   ' .. name)
    else
        failures = failures + 1
        print('FAIL ' .. name .. '\n     got:  ' .. tostring(got) .. '\n     want: ' .. tostring(want))
    end
end

local function last_line(path)
    local last
    for line in (files[path] or ''):gmatch('[^\n]+') do last = line end
    return last
end

local function count_lines(path)
    return select(2, (files[path] or ''):gsub('\n', ''))
end

local function chat_has(fragment)
    for _, line in ipairs(chat) do
        if line:find(fragment, 1, true) then return true end
    end
    return false
end

-------------------------------------------------------------------------------
-- packs.decode

local packs = require('packs')

local v = packs.decode('{"a": [1, 2.5, -3e2, true, false, null, "x"], "b": {"c": "d"}, "e": ""}')
check('json object', v.b.c, 'd')
check('json numbers', v.a[1] + v.a[2] + v.a[3], -296.5)
check('json booleans', tostring(v.a[4]) .. tostring(v.a[5]), 'truefalse')
check('json null leaves a hole', v.a[6], nil)
check('json keeps elements after a null', v.a[7], 'x')
check('json empty string', v.e, '')
check('json escapes', packs.decode([["a\"b\\c\/d\n\t"]]), 'a"b\\c/d\n\t')
check('json unicode escape', packs.decode([["Café"]]), 'Café')
check('json surrogate pair', packs.decode([["😀"]]), string.char(0xF0, 0x9F, 0x98, 0x80))
check('json raw utf-8 passes through', packs.decode('"Vana\'diel é"'), "Vana'diel é")
check('json empty containers', #packs.decode('[]') + #packs.decode('{}'), 0)
check('json whitespace', packs.decode(' \n[ 1 ,\n2 ]\n')[2], 2)
check('json bad input', select(2, packs.decode('{"a" 1}')), 'expected : at line 1')
check('json trailing junk', select(2, packs.decode('[1]\n\nx')), 'trailing characters at line 3')
check('json unterminated string', select(2, packs.decode('"abc')), 'unterminated string at line 1')
check('json not a string', select(2, packs.decode(nil)), 'not a string')

check('version compare', packs.newer('0.10', '0.9') and packs.newer('1.0', '0.9.9') and not packs.newer('0.1', '0.1'), true)

-------------------------------------------------------------------------------
-- packs.load over the in-memory tree

local fs = {
    get_dir = windower.get_dir, dir_exists = windower.dir_exists, file_exists = windower.file_exists,
    read = function(path) local f = io.open(path, 'rb'); return f and f:read('*a') end,
}
local index = packs.load(AUDIO, fs)
check('two packs installed', #index.packs, 2)
check('packs sorted by name', index.packs[1].name .. ' ' .. index.packs[2].name, 'core rhapsodies')
check('highest version wins', index.packs[2].version, '0.1')
check('line count', index.lines, 7)
check('duplicate across packs counted', index.conflicts, 1)
check('lookup gives a windows path', packs.lookup(index, 'Iroha', 'Welcome, traveler.').path,
    AUDIO .. '\\rhapsodies\\0.1\\Iroha\\welcome-traveler.aaaa.opus')
check('lookup carries the pack', packs.lookup(index, 'Iroha', 'Welcome, traveler.').pack, 'rhapsodies')
check('escaped quotes in the key', packs.lookup(index, 'Gilgamesh', 'Yar, "Iroha" said so.') ~= nil, true)
check('unicode escape in the key', packs.lookup(index, 'Cait Sith', 'Café time.') ~= nil, true)
check('first pack by name wins a duplicate', packs.lookup(index, '', 'Narration.').pack, 'core')
check('unknown line', packs.lookup(index, 'Iroha', 'Welcome traveler'), nil)

local function has_warning(fragment)
    for _, w in ipairs(index.warnings) do if w:find(fragment, 1, true) then return true end end
    return false
end
check('part directory warned', has_warning('rhapsodies 0.2.part: download in progress'), true)
check('older version warned', has_warning('rhapsodies 0.0.9: older than 0.1'), true)
check('empty pack warned', has_warning('empty: empty'), true)
check('warning count', #index.warnings, 3)

local d = packs.diagnose(index, 'Lion', 'Welcome, traveler.')
check('same text under other names', table.concat(d.speakers_with_text, '|'), "Iroha's Voice|Iroha")
d = packs.diagnose(index, 'Iroha', 'Welcome traveler!')
check('near match by folded text', d.near[1].text, 'Welcome, traveler.')
check('exact line is not near itself', #packs.diagnose(index, 'Iroha', 'Welcome, traveler.').near, 0)
check('describe', packs.describe(index)[1], 'core 0.4 (3 lines)')
check('describe counts a duplicate once', packs.describe(index)[2], 'rhapsodies 0.1 (4 lines)')
check('describe nothing', packs.describe(packs.load('./nowhere', fs))[1], 'no audio packs installed under ./nowhere')

-------------------------------------------------------------------------------
-- the addon loads

dofile('Spoken.lua')

local incoming_text = assert(handlers['incoming text'])
local incoming_chunk = assert(handlers['incoming chunk'])
local command = assert(handlers['addon command'])
local prerender = assert(handlers['prerender'])
local unload = assert(handlers['unload'])

local PROMPT = string.char(0x7F, 0x31)
local AUTO_PROMPT = string.char(0x7F, 0x34, 0x01)
local QUEUE = './data/queue.jsonl'
local STATUS = './data/status.json'
local MISSES = './data/misses.jsonl'

check('load reports the mode', chat_has('mode audio, player fake 0'), true)
check('load reports the packs', chat_has('rhapsodies 0.1 (4 lines)'), true)
check('load reports warnings', chat_has('3 pack warning(s)'), true)
check('volume applied at load', fake.volume, 1)

-------------------------------------------------------------------------------
-- text.clean

local text = require('text')

local speaker, line, prompted = text.clean('Iroha : Welcome, traveler.' .. PROMPT)
local _
check('speaker extracted', speaker, 'Iroha')
check('prompt stripped', line, 'Welcome, traveler.')
check('prompted detected', prompted, true)

speaker, line, prompted = text.clean('Some narration here.')
check('no speaker for narration', speaker, '')
check('narration kept', line, 'Some narration here.')
check('not prompted', prompted, false)

_, line = text.clean('Iroha : Bring ' .. string.char(0x1E, 0x03) .. 'the key' .. string.char(0x1E, 0x01) .. ' now.')
check('colour codes removed', line, 'Bring the key now.')

_, line = text.clean('Iroha : First line.' .. string.char(0x07) .. 'Second line.')
check('line break becomes a space', line, 'First line. Second line.')

_, line = text.clean('Iroha : ' .. string.char(0xEF, 0x1F) .. ' and ' .. string.char(0xEF, 0x24) .. ' crystals')
check('element symbols become words', line, 'Fire and Water crystals')

_, line = text.clean('Hegouard : Make merry this night!' .. string.char(0x81, 0xED) .. PROMPT)
check('music note dropped', line, 'Make merry this night!')

_, line = text.clean('Iroha : ' .. string.char(0x81, 0x40) .. 'indented' .. string.char(0x81, 0x99))
check('ideographic space and star handled', line, 'indented')

check('auto prompt detected', text.is_auto_prompt(AUTO_PROMPT), true)

-- text.player_token (mirrored in tools/test_ffxitext.py)
local function tokened(spk, ln) local a, b = text.player_token(spk, ln, 'Tester'); return a .. '|' .. b end
check('player name becomes the token', tokened('Iroha', 'Welcome, Tester.'), 'Iroha|Welcome, <player>.')
check('possessive keeps its s', tokened('Iroha', "Tester's legend."), "Iroha|<player>'s legend.")
check('every occurrence', tokened('Iroha', 'Tester, Tester!'), 'Iroha|<player>, <player>!')
check('whole words only', tokened('Iroha', 'Testers and Tester.'), 'Iroha|Testers and <player>.')
check('inside a word untouched', tokened('Iroha', 'Attesters.'), 'Iroha|Attesters.')
check('the player as speaker', tokened('Tester', 'Yes.'), '<player>|Yes.')
check('no name, no change', select(2, text.player_token('Iroha', 'Tester.', nil)), 'Tester.')
check('token constant', text.PLAYER, '<player>')
check('plain prompt is not auto', text.is_auto_prompt('x' .. PROMPT), false)

-------------------------------------------------------------------------------
-- audio mode: hits

local WELCOME = AUDIO .. '\\rhapsodies\\0.1\\Iroha\\welcome-traveler.aaaa.opus'
local raw = 'Iroha : Welcome, traveler.' .. PROMPT
check('log line untouched', incoming_text(raw, raw, 150), nil)
check('a known line is played', last_call(), 'play ' .. WELCOME)
check('nothing queued for the helper', files[QUEUE], nil)

incoming_text('Iroha : Well met, Tester.', 'Iroha : Well met, Tester.', 150)
check('player name tokened before lookup', last_call(), 'play ' .. AUDIO .. '\\rhapsodies\\0.1\\Iroha\\well-met-player.bbbb.opus')

incoming_text('Hello', 'Hello', 1)
check('other chat modes ignored', #fake.calls, 2)

incoming_text('Narration.', 'Narration.', 151)
check('narration off by default', #fake.calls, 2)
command('system')
incoming_text('Narration.', 'Narration.', 151)
check('narration played when enabled', last_call(), 'play ' .. AUDIO .. '\\core\\0.4\\narration\\narration.hhhh.opus')
command('system')

-------------------------------------------------------------------------------
-- audio mode: misses and the log

fake.calls = {}
raw = 'Zonpa-Zippa : Hello there.' .. PROMPT
incoming_text(raw, raw, 150)
check('unknown line stops the previous one', last_call(), 'stop')
check('unknown line is not played', #fake.calls, 1)
check('no miss log by default', files[MISSES], nil)

chat = {}
command('log')
check('log toggled', chat[1], 'miss log (data/misses.jsonl) on')
incoming_text(raw, raw, 150)
local miss = last_line(MISSES)
check('miss logged', miss ~= nil, true)
check('miss reason', miss:match('"reason": "([^"]*)"'), 'unknown')
check('miss speaker and text', miss:match('"speaker": "([^"]*)", "text": "([^"]*)"'), 'Zonpa-Zippa')
check('miss prompted and mode', miss:match('"prompted": true, "mode": 150') ~= nil, true)
check('miss raw bytes escaped', miss:match('"raw": "([^"]*)"'), 'Zonpa-Zippa : Hello there.\\u007f1')
check('miss player name', miss:match('"player": "([^"]*)"'), 'Tester')
check('miss zone', miss:match('"zone": (%d+)'), '231')
check('miss position', miss:match('"pos": (%b{})'), '{"x": 1.500, "y": -2, "z": 0}')
check('miss target', miss:match('"target": (%b{})'), '{"name": "Iroha", "id": 17000001, "index": 123}')
check('miss packs', miss:match('"packs": (%b[])'), '["core 0.4 (3 lines)", "rhapsodies 0.1 (4 lines)"]')
check('miss with nothing near', miss:match('"speakers_with_text": %[%], "near": %[%]') ~= nil, true)

raw = 'Iroha : Welcome traveler!'
incoming_text(raw, raw, 150)
miss = last_line(MISSES)
check('near line reported', miss:match('"near": (%b[])'), '[{"speaker": "Iroha", "text": "Welcome, traveler."}]')

raw = 'Lion : Welcome, traveler.'
incoming_text(raw, raw, 150)
check('other speakers of the text reported', last_line(MISSES):match('"speakers_with_text": (%b[])'), '["Iroha\'s Voice", "Iroha"]')

raw = 'Gilgamesh : Yar, "Iroha" said so.'
incoming_text(raw, raw, 150)
miss = last_line(MISSES)
check('missing file reason', miss:match('"reason": "([^"]*)"'), 'file_missing')
check('missing file path', miss:match('"detail": "([^"]*)"'), AUDIO .. '\\\\rhapsodies\\\\0.1\\\\Gilgamesh\\\\yar-iroha-said-so.cccc.opus')
check('quotes escaped in the log', miss:match('"text": (".-"), "prompted"'), '"Yar, \\"Iroha\\" said so."')

fake.fail = 'device busy'
raw = 'Iroha : Welcome, traveler.'
incoming_text(raw, raw, 150)
miss = last_line(MISSES)
check('player failure logged', miss:match('"reason": "([^"]*)", "detail": "([^"]*)"'), 'play_failed')
check('player failure detail', select(2, miss:match('"reason": "([^"]*)", "detail": "([^"]*)"')), 'device busy')
fake.fail = nil

local misses_before = count_lines(MISSES)
command('log')
incoming_text('Nobody : Nothing.', 'Nobody : Nothing.', 150)
check('log off writes nothing', count_lines(MISSES), misses_before)

-------------------------------------------------------------------------------
-- audio mode: stopping

command('stop')
fake.calls = {}
incoming_text(AUTO_PROMPT, AUTO_PROMPT, 150)
check('auto prompt stops nothing when no line is current', #fake.calls, 0)

incoming_text(raw, raw, 150)
incoming_text(AUTO_PROMPT, AUTO_PROMPT, 150)
check('auto prompt stops playback', last_call(), 'stop')

incoming_text(raw, raw, 150)
incoming_chunk(0x52)
check('release packet stops playback', last_call(), 'stop')
local calls_before = #fake.calls
incoming_chunk(0x52)
check('second release does nothing', #fake.calls, calls_before)

incoming_text(raw, raw, 150)
incoming_chunk(0x0B)
check('zoning stops playback', last_call(), 'stop')

incoming_text(raw, raw, 150)
command('stop')
check('stop command stops playback', last_call(), 'stop')

command('off')
calls_before = #fake.calls
incoming_text(raw, raw, 150)
check('off plays nothing', #fake.calls, calls_before)
command('on')

-------------------------------------------------------------------------------
-- audio mode: auto-advance from the player's state

commands = {}
raw = 'Iroha : Welcome, traveler.' .. PROMPT
incoming_text(raw, raw, 150)
fake.st = 'done'
for _ = 1, 12 do prerender() end
check('no auto-advance by default', #commands, 0)

command('auto')
for _ = 1, 12 do prerender() end
check('auto-advance sends enter once', #commands, 1)
check('setkey used', commands[1]:find('setkey enter') ~= nil, true)
for _ = 1, 12 do prerender() end
check('does not advance twice for one line', #commands, 1)

incoming_text(raw, raw, 150)
check('a new line resets the player state', fake.st, 'playing')
for _ = 1, 12 do prerender() end
check('still playing, no advance', #commands, 1)
fake.st = 'done'
for _ = 1, 12 do prerender() end
check('next line advances again', #commands, 2)

incoming_text('Iroha : Welcome, traveler.', 'Iroha : Welcome, traveler.', 144)
fake.st = 'done'
for _ = 1, 12 do prerender() end
check('unprompted line never auto-advances', #commands, 2)

incoming_text('Nobody : Missed.' .. PROMPT, 'Nobody : Missed.' .. PROMPT, 150)
fake.st = 'done'
for _ = 1, 12 do prerender() end
check('a missed line never auto-advances', #commands, 2)
command('auto')

-------------------------------------------------------------------------------
-- audio mode: commands

chat = {}
command('volume', '50')
check('volume reported', chat[1], 'volume 50')
check('volume passed to the player', fake.volume, 0.5)
command('volume', '250')
check('volume clamped', fake.volume, 1)

chat = {}
command('packs')
check('packs lists each pack', chat[1] .. ' / ' .. chat[2], 'core 0.4 (3 lines) / rhapsodies 0.1 (4 lines)')
check('packs lists the warnings', chat_has('rhapsodies 0.2.part: download in progress'), true)
check('packs lists the duplicates', chat_has('1 lines appear in more than one pack'), true)

chat = {}
command('reload')
check('reload rescans', chat_has('rhapsodies 0.1 (4 lines)'), true)

chat = {}
command('test', 'Iroha', ':', 'Well', 'met,', 'Tester.')
check('test plays through the lookup', chat[1], 'playing ' .. AUDIO .. '\\rhapsodies\\0.1\\Iroha\\well-met-player.bbbb.opus')
chat = {}
command('test', 'Nobody', ':', 'Missed.')
check('test reports a miss', chat[1], 'missed (unknown) Nobody: Missed.')

chat = {}
fake.st = 'playing'
command('status')
check('status reports the mode', chat[1], 'mode audio, player fake 0')
check('status reports the player', chat_has('player: playing'), true)
check('status reports the miss', chat_has('missed: unknown'), true)

chat = {}
command('mode', 'sideways')
check('bad mode rejected', chat[1], 'modes are audio and helper')

unload()
check('unload shuts the player down', last_call(), 'shutdown')

-------------------------------------------------------------------------------
-- helper mode: incoming text writes the queue

chat = {}
command('mode', 'helper')
check('mode switched', chat[#chat], 'mode helper')
check('switching modes stops the player', last_call(), 'stop')
fake.calls = {}

files[QUEUE] = nil
raw = 'Iroha : Welcome, traveler.' .. PROMPT
incoming_text(raw, raw, 150)
local seq0 = tonumber((last_line(QUEUE)):match('"seq": (%d+)'))
check('speak entry written', (last_line(QUEUE)):gsub('"t": %d+', '"t": T'),
    ('{"seq": %d, "speaker": "Iroha", "text": "Welcome, traveler.", "prompted": true, "mode": 150, "t": T}'):format(seq0))
check('player not used in helper mode', #fake.calls, 0)

incoming_text('Iroha : Well met, Tester.', 'Iroha : Well met, Tester.', 150)
check('queue entry carries the token', (last_line(QUEUE)):match('"text": "([^"]*)"'), 'Well met, <player>.')

incoming_text('Hello', 'Hello', 1)
check('other chat modes ignored', count_lines(QUEUE), 2)

incoming_text('Narration.', 'Narration.', 151)
check('system narration off by default', count_lines(QUEUE), 2)

command('system')
incoming_text('Narration.', 'Narration.', 151)
check('system narration on when enabled', (last_line(QUEUE)):match('"text": "([^"]*)"'), 'Narration.')
command('system')

raw = 'Iroha : "Quoted" and back\\slash'
incoming_text(raw, raw, 144)
check('json escaping', (last_line(QUEUE)):match('"text": (".-"), "prompted"'), '"\\"Quoted\\" and back\\\\slash"')
check('timed line not prompted', (last_line(QUEUE)):match('"prompted": (%a+)'), 'false')

incoming_text(AUTO_PROMPT, AUTO_PROMPT, 150)
check('auto prompt writes a stop', (last_line(QUEUE)):match('"stop": true, "reason": "(%a+)"'), 'advance')

incoming_text(raw, raw, 150)
incoming_chunk(0x52)
check('release packet writes a stop', (last_line(QUEUE)):match('"reason": "(%a+)"'), 'release')
local before = files[QUEUE]
incoming_chunk(0x52)
check('second release writes nothing', files[QUEUE], before)

incoming_text(raw, raw, 150)
incoming_chunk(0x0B)
check('zoning writes a stop', (last_line(QUEUE)):match('"reason": "(%a+)"'), 'zone')

command('off')
incoming_text(raw, raw, 150)
check('off writes nothing', (last_line(QUEUE)):match('"reason": "(%a+)"'), 'zone')
command('on')

-------------------------------------------------------------------------------
-- helper mode: auto-advance from status.json

commands = {}
raw = 'Iroha : Prompted.' .. PROMPT
incoming_text(raw, raw, 150)
local seq = tonumber((last_line(QUEUE)):match('"seq": (%d+)'))
files[STATUS] = ('{"seq": %d, "state": "done"}'):format(seq)
for _ = 1, 12 do prerender() end
check('no auto-advance by default', #commands, 0)

command('auto')
for _ = 1, 12 do prerender() end
check('auto-advance sends enter once', #commands, 1)
check('setkey used', commands[1]:find('setkey enter') ~= nil, true)
for _ = 1, 12 do prerender() end
check('does not advance twice for one seq', #commands, 1)

files[STATUS] = ('{"seq": %d, "state": "done"}'):format(seq - 1)
raw = 'Iroha : Next.' .. PROMPT
incoming_text(raw, raw, 150)
for _ = 1, 12 do prerender() end
check('stale status ignored', #commands, 1)

raw = 'Iroha : Timed.'
incoming_text(raw, raw, 144)
seq = tonumber((last_line(QUEUE)):match('"seq": (%d+)'))
files[STATUS] = ('{"seq": %d, "state": "done"}'):format(seq)
for _ = 1, 12 do prerender() end
check('unprompted line never auto-advances', #commands, 1)
command('auto')

-------------------------------------------------------------------------------
-- helper mode: commands

chat = {}
command('test', 'Tester', ':', 'A', 'test', 'line.')
check('//spoken test queues a line', (last_line(QUEUE)):match('"text": "([^"]*)"'), 'A test line.')
check('//spoken test tokens the player as speaker', (last_line(QUEUE)):match('"speaker": "([^"]*)"'), '<player>')

chat = {}
command('status')
check('status reports the helper line', chat_has('helper: seq'), true)

chat = {}
command('mode', 'audio')
check('back to audio', chat[#chat], 'mode audio, player fake 0')

chat = {}
command('bogus')
check('unknown command is reported', chat[1], 'unknown command; //spoken help')

-------------------------------------------------------------------------------

if failures > 0 then
    print(failures .. ' failure(s)')
    os.exit(1)
end
print('all tests passed')
