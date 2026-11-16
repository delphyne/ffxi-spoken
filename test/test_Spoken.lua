-- Tests for Spoken's capture pipeline, run outside the game:
--   luajit test/test_Spoken.lua        (from the addon directory)
-- Windower's globals and libs (luau, config, chat.chars) are stubbed, and
-- io.open is redirected into memory so the queue the addon writes can be
-- read back.

package.path = './?.lua;' .. package.path

local chat = {}
local handlers = {}
local files = {}
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
-- windower stubs

_addon = {}
windower = {
    addon_path = './',
    register_event = function(name, fn) handlers[name] = fn end,
    add_to_chat = function(_, line) chat[#chat + 1] = line end,
    from_shift_jis = function(s) return s end,
    send_command = function(cmd) commands[#commands + 1] = cmd end,
    ffxi = {get_player = function() return {name = 'Tester'} end},
}

-- in-memory files: io.open returns handles over the `files` table for the
-- whole run, since the addon opens the queue on every line
io.open = function(path, mode)
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

dofile('Spoken.lua')

local incoming_text = assert(handlers['incoming text'])
local incoming_chunk = assert(handlers['incoming chunk'])
local command = assert(handlers['addon command'])
local prerender = assert(handlers['prerender'])

local PROMPT = string.char(0x7F, 0x31)
local AUTO_PROMPT = string.char(0x7F, 0x34, 0x01)
local QUEUE = './data/queue.jsonl'
local STATUS = './data/status.json'

local failures = 0
local function check(name, got, want)
    if got == want then
        print('ok   ' .. name)
    else
        failures = failures + 1
        print('FAIL ' .. name .. '\n     got:  ' .. tostring(got) .. '\n     want: ' .. tostring(want))
    end
end

local function last_queue_line()
    local last
    for line in (files[QUEUE] or ''):gmatch('[^\n]+') do last = line end
    return last
end

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
-- incoming text writes the queue

files = {}
local raw = 'Iroha : Welcome, traveler.' .. PROMPT
check('log line untouched', incoming_text(raw, raw, 150), nil)
check('speak entry written', (last_queue_line()):gsub('"t": %d+', '"t": T'),
    '{"seq": 1, "speaker": "Iroha", "text": "Welcome, traveler.", "prompted": true, "mode": 150, "t": T}')

incoming_text('Iroha : Well met, Tester.', 'Iroha : Well met, Tester.', 150)
check('queue entry carries the token', (last_queue_line()):match('"text": "([^"]*)"'), 'Well met, <player>.')

incoming_text('Hello', 'Hello', 1)
check('other chat modes ignored', select(2, (files[QUEUE]):gsub('\n', '')), 2)

incoming_text('Narration.', 'Narration.', 151)
check('system narration off by default', select(2, (files[QUEUE]):gsub('\n', '')), 2)

command('system')
incoming_text('Narration.', 'Narration.', 151)
check('system narration on when enabled', (last_queue_line()):match('"text": "([^"]*)"'), 'Narration.')
command('system')

raw = 'Iroha : "Quoted" and back\\slash'
incoming_text(raw, raw, 144)
check('json escaping', (last_queue_line()):match('"text": (".-"), "prompted"'), '"\\"Quoted\\" and back\\\\slash"')
check('timed line not prompted', (last_queue_line()):match('"prompted": (%a+)'), 'false')

incoming_text(AUTO_PROMPT, AUTO_PROMPT, 150)
check('auto prompt writes a stop', (last_queue_line()):match('"stop": true, "reason": "(%a+)"'), 'advance')

incoming_text(raw, raw, 150)
incoming_chunk(0x52)
check('release packet writes a stop', (last_queue_line()):match('"reason": "(%a+)"'), 'release')
local before = files[QUEUE]
incoming_chunk(0x52)
check('second release writes nothing', files[QUEUE], before)

incoming_text(raw, raw, 150)
incoming_chunk(0x0B)
check('zoning writes a stop', (last_queue_line()):match('"reason": "(%a+)"'), 'zone')

command('off')
incoming_text(raw, raw, 150)
check('off writes nothing', (last_queue_line()):match('"reason": "(%a+)"'), 'zone')
command('on')

-------------------------------------------------------------------------------
-- auto-advance from status.json

commands = {}
raw = 'Iroha : Prompted.' .. PROMPT
incoming_text(raw, raw, 150)
local seq = tonumber((last_queue_line()):match('"seq": (%d+)'))
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
seq = tonumber((last_queue_line()):match('"seq": (%d+)'))
files[STATUS] = ('{"seq": %d, "state": "done"}'):format(seq)
for _ = 1, 12 do prerender() end
check('unprompted line never auto-advances', #commands, 1)
command('auto')

-------------------------------------------------------------------------------
-- commands

chat = {}
command('test', 'Tester', ':', 'A', 'test', 'line.')
check('//spoken test queues a line', (last_queue_line()):match('"text": "([^"]*)"'), 'A test line.')
check('//spoken test speaker', (last_queue_line()):match('"speaker": "([^"]*)"'), 'Tester')

chat = {}
command('status')
check('status reports the helper line', chat[1]:find('helper: seq') ~= nil, true)

chat = {}
command('bogus')
check('unknown command is reported', chat[1], 'unknown command; //spoken help')

-------------------------------------------------------------------------------

if failures > 0 then
    print(failures .. ' failure(s)')
    os.exit(1)
end
print('all tests passed')
