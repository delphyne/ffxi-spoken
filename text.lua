-- Turns a raw NPC dialogue line from the chat log into something a voice
-- can read: speaker name, clean UTF-8 text, and whether the game is waiting
-- for the player.
--
-- The order matters and mirrors Balloon: substitute FFXI's private byte
-- codes while the string is still Shift-JIS, convert, then tidy the
-- Unicode. Keep every byte-level fact in this file, with a test for each.

local text = {}

local ok, chars = pcall(require, 'chat.chars')
if not ok then chars = {} end

-- 0x7F 0x31..0x33, 0x37: the game is waiting for the player
local PROMPT = string.char(0x7F) .. '[\49-\51\55]'
-- 0x7F 0x34..0x36 followed by a seconds byte: continues by itself
local AUTO_PROMPT = string.char(0x7F) .. '[\52-\54].'

-- 0xEF 0x1F..0x26 are the element symbols
local ELEMENTS = {
    [0x1F] = 'Fire', [0x20] = 'Ice', [0x21] = 'Wind', [0x22] = 'Earth',
    [0x23] = 'Lightning', [0x24] = 'Water', [0x25] = 'Light', [0x26] = 'Dark',
}

-- private Shift-JIS codes the converter cannot handle, see Balloon's
-- SubCharactersPreShift and Windower's chat/chars.lua
local SJIS_SUBS = {
    {string.char(0x81, 0x40), ' '},          -- ideographic space / tab
    {string.char(0x81, 0xF4), ''},           -- standard music note
    {chars.note or string.char(0x81, 0xED), ''},  -- the game's music note
    {chars.sharp or string.char(0x81, 0xEB), ''},
    {chars.flat or string.char(0x81, 0xEC), ''},
    {chars.bstar or string.char(0x81, 0x99), ''},
    {chars.wstar or string.char(0x81, 0x9A), ''},
    {chars.wave or string.char(0x81, 0x60), '~'},
    {string.char(0x87, 0xB2), '"'},          -- centred left double quote
    {string.char(0x87, 0xB3), '"'},          -- centred right double quote
    {string.char(0x88, 0x69), 'é'},
}

local function plain(s)
    return (s:gsub('%p', '%%%0'))
end

-- Returns true when the line ends in a prompt, and the line without it.
local function strip_prompt(raw)
    local prompted = raw:find(PROMPT .. '$') ~= nil
    raw = raw:gsub(PROMPT, '')
    raw = raw:gsub(AUTO_PROMPT, '')
    return raw, prompted
end

function text.is_auto_prompt(raw)
    return raw:find(AUTO_PROMPT .. '$') ~= nil
end

-- speaker, line, prompted
function text.clean(raw)
    local line, prompted = strip_prompt(raw)

    -- "Name : " prefix; the name is short and the separator is fixed
    local speaker = ''
    local s, e = line:find('^.- : ')
    if s and e < 32 then
        speaker = line:sub(s, e - 3):gsub('^%s+', '')
        line = line:sub(e + 1)
    end

    -- element symbols become words before conversion mangles the bytes
    line = line:gsub(string.char(0xEF) .. '(.)', function(c)
        local name = ELEMENTS[c:byte()]
        return name and (' ' .. name .. ' ') or ''
    end)

    for _, sub in ipairs(SJIS_SUBS) do
        line = line:gsub(plain(sub[1]), sub[2])
    end

    line = windower.from_shift_jis(line)

    -- colour codes (0x1E n, 0x1F n), line breaks (0x07), anything else below space
    line = line:gsub('[\30\31].', '')
    line = line:gsub('\7', ' ')
    line = line:gsub('[%z\1-\31\127]', '')

    -- collapse whitespace
    line = line:gsub('%s+', ' '):gsub('^%s', ''):gsub('%s$', '')

    return speaker, line, prompted
end

-- The token a pack line carries where the game printed the player's name,
-- so the same line is one key for every player (docs/Audio-Packs.md).
text.PLAYER = '<player>'

-- Replaces every whole-word occurrence of the player's name in a cleaned
-- line with the token, and the speaker when the player is the one talking.
-- Whole words only: a player called Lion must not swallow the NPC Lion in
-- "Lionel". Names are letters only, so the frontier is a letter boundary.
function text.player_token(speaker, line, name)
    if not name or name == '' then return speaker, line end
    local pattern = '%f[%a]' .. plain(name) .. '%f[%A]'
    line = line:gsub(pattern, text.PLAYER)
    if speaker == name then speaker = text.PLAYER end
    return speaker, line
end

return text
