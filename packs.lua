-- Installed audio packs: finds every audio/<pack>/<version>/manifest.json,
-- builds the lookup index in memory, and answers "which file speaks this
-- line" by speaker plus cleaned text (docs/Audio-Packs.md). Pure Lua; the
-- file system is passed in so the tests can run outside the game.

local packs = {}

-------------------------------------------------------------------------------
-- JSON
--
-- Windower ships libs/json.lua, but it tokenises character by character and
-- does not understand escaped quotes, and dialogue is full of them. This is
-- a strict decoder: objects to tables, arrays to arrays, null to nil.

local ESCAPES = {['"'] = '"', ['\\'] = '\\', ['/'] = '/', b = '\b', f = '\f', n = '\n', r = '\r', t = '\t'}

local function utf8_char(cp)
    if cp < 0x80 then return string.char(cp) end
    if cp < 0x800 then
        return string.char(0xC0 + math.floor(cp / 0x40), 0x80 + cp % 0x40)
    end
    if cp < 0x10000 then
        return string.char(0xE0 + math.floor(cp / 0x1000), 0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
    end
    return string.char(0xF0 + math.floor(cp / 0x40000), 0x80 + math.floor(cp / 0x1000) % 0x40,
        0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
end

local function fail(pos, what)
    error({pos = pos, what = what}, 0)
end

local function skip_space(s, pos)
    return s:find('%S', pos) or #s + 1
end

-- pos is at the opening quote; returns the string and the position after it
local function parse_string(s, pos)
    local parts = {}
    local i = pos + 1
    while true do
        local j = s:find('["\\]', i)
        if not j then fail(pos, 'unterminated string') end
        if j > i then parts[#parts + 1] = s:sub(i, j - 1) end
        if s:byte(j) == 34 then return table.concat(parts), j + 1 end
        local c = s:sub(j + 1, j + 1)
        if c == 'u' then
            local hex = s:match('^%x%x%x%x', j + 2)
            if not hex then fail(j, 'bad \\u escape') end
            local cp = tonumber(hex, 16)
            i = j + 6
            if cp >= 0xD800 and cp <= 0xDBFF then
                local low = s:match('^\\u(%x%x%x%x)', i)
                local lo = low and tonumber(low, 16)
                if lo and lo >= 0xDC00 and lo <= 0xDFFF then
                    cp = 0x10000 + (cp - 0xD800) * 0x400 + (lo - 0xDC00)
                    i = i + 6
                end
            end
            parts[#parts + 1] = utf8_char(cp)
        else
            local r = ESCAPES[c]
            if not r then fail(j, 'bad escape') end
            parts[#parts + 1] = r
            i = j + 2
        end
    end
end

local parse_value

local function parse_array(s, pos)
    local out, n = {}, 0
    pos = skip_space(s, pos + 1)
    if s:byte(pos) == 93 then return out, pos + 1 end  -- ]
    while true do
        n = n + 1
        out[n], pos = parse_value(s, pos)
        pos = skip_space(s, pos)
        local c = s:byte(pos)
        if c == 93 then return out, pos + 1 end
        if c ~= 44 then fail(pos, 'expected , or ]') end
        pos = skip_space(s, pos + 1)
    end
end

local function parse_object(s, pos)
    local out = {}
    pos = skip_space(s, pos + 1)
    if s:byte(pos) == 125 then return out, pos + 1 end  -- }
    while true do
        if s:byte(pos) ~= 34 then fail(pos, 'expected a key') end
        local key
        key, pos = parse_string(s, pos)
        pos = skip_space(s, pos)
        if s:byte(pos) ~= 58 then fail(pos, 'expected :') end
        out[key], pos = parse_value(s, skip_space(s, pos + 1))
        pos = skip_space(s, pos)
        local c = s:byte(pos)
        if c == 125 then return out, pos + 1 end
        if c ~= 44 then fail(pos, 'expected , or }') end
        pos = skip_space(s, pos + 1)
    end
end

function parse_value(s, pos)
    local c = s:byte(pos)
    if c == 34 then return parse_string(s, pos) end
    if c == 123 then return parse_object(s, pos) end
    if c == 91 then return parse_array(s, pos) end
    if s:find('^true', pos) then return true, pos + 4 end
    if s:find('^false', pos) then return false, pos + 5 end
    if s:find('^null', pos) then return nil, pos + 4 end
    local num = s:match('^-?%d+%.?%d*[eE]?[-+]?%d*', pos)
    local n = num and tonumber(num)
    if not n then fail(pos, 'unexpected character') end
    return n, pos + #num
end

-- value, or nil and a message
function packs.decode(s)
    if type(s) ~= 'string' then return nil, 'not a string' end
    local ok, value, pos = pcall(function()
        local v, p = parse_value(s, skip_space(s, 1))
        p = skip_space(s, p)
        if p <= #s then fail(p, 'trailing characters') end
        return v, p
    end)
    if not ok then
        local e = value
        if type(e) == 'table' then
            local line = select(2, s:sub(1, e.pos):gsub('\n', '')) + 1
            return nil, ('%s at line %d'):format(e.what, line)
        end
        return nil, tostring(e)
    end
    return value, pos
end

-------------------------------------------------------------------------------
-- packs

local function key(speaker, text)
    return speaker .. '\n' .. text
end

-- letters and digits only, lower case: two lines that differ by punctuation,
-- spacing, or a glyph word fold to the same string
local function fold(s)
    return (s:lower():gsub('[^a-z0-9]', ''))
end

local function version_parts(v)
    local parts = {}
    for p in v:gmatch('[^.]+') do parts[#parts + 1] = tonumber(p) or p end
    return parts
end

-- "0.10" sorts after "0.9"; a non-numeric part compares as a string
function packs.newer(a, b)
    local pa, pb = version_parts(a), version_parts(b)
    for i = 1, math.max(#pa, #pb) do
        local x, y = pa[i], pb[i]
        if x == nil then return false end
        if y == nil then return true end
        if type(x) ~= type(y) then x, y = tostring(x), tostring(y) end
        if x ~= y then return x > y end
    end
    return false
end

local function sorted(list)
    local out = {}
    for _, v in ipairs(list or {}) do out[#out + 1] = v end
    table.sort(out)
    return out
end

local function add(list_by_key, k, value)
    local list = list_by_key[k]
    if not list then
        list = {}
        list_by_key[k] = list
    end
    list[#list + 1] = value
end

-- The index over every installed pack under root. fs supplies get_dir(path)
-- (names in a directory), dir_exists, file_exists, and read(path) (the
-- whole file or nil). Paths handed back use backslashes: they go to the
-- native player on Windows.
function packs.load(root, fs)
    local index = {
        root = root,
        packs = {},        -- {name, version, dir, lines}
        by_key = {},       -- speaker\ntext -> {pack, version, path, duration}
        by_text = {},      -- text -> {speakers}
        by_fold = {},      -- fold(speaker)\nfold(text) -> {{speaker, text}}
        warnings = {},
        lines = 0,
        conflicts = 0,
    }
    local function warn(fmt, ...) index.warnings[#index.warnings + 1] = fmt:format(...) end

    if not fs.dir_exists(root) then return index end

    for _, name in ipairs(sorted(fs.get_dir(root))) do
        local pack_dir = root .. '\\' .. name
        if not name:find('%.part$') and fs.dir_exists(pack_dir) then
            local best
            for _, version in ipairs(sorted(fs.get_dir(pack_dir))) do
                local dir = pack_dir .. '\\' .. version
                if version:find('%.part$') then
                    warn('%s %s: download in progress, ignored', name, version)
                elseif not fs.file_exists(dir .. '\\manifest.json') then
                    warn('%s %s: no manifest.json, ignored', name, version)
                elseif best and not packs.newer(version, best) then
                    warn('%s %s: older than %s, ignored', name, version, best)
                else
                    if best then warn('%s %s: older than %s, ignored', name, best, version) end
                    best = version
                end
            end
            if best then
                local dir = pack_dir .. '\\' .. best
                local manifest, err = packs.decode(fs.read(dir .. '\\manifest.json'))
                if type(manifest) ~= 'table' or type(manifest.lines) ~= 'table' then
                    warn('%s %s: manifest unreadable (%s)', name, best, err or 'no lines')
                else
                    local count = 0
                    for _, row in ipairs(manifest.lines) do
                        if type(row.speaker) == 'string' and type(row.text) == 'string' and type(row.file) == 'string' then
                            local k = key(row.speaker, row.text)
                            if index.by_key[k] then
                                index.conflicts = index.conflicts + 1
                            else
                                index.by_key[k] = {
                                    pack = name, version = best, duration = row.duration,
                                    path = dir .. '\\' .. row.file:gsub('/', '\\'),
                                }
                                add(index.by_text, row.text, row.speaker)
                                add(index.by_fold, key(fold(row.speaker), fold(row.text)), {speaker = row.speaker, text = row.text})
                                count = count + 1
                            end
                        end
                    end
                    index.packs[#index.packs + 1] = {name = name, version = best, dir = dir, lines = count}
                    index.lines = index.lines + count
                end
            elseif #(fs.get_dir(pack_dir) or {}) == 0 then
                warn('%s: empty, no version installed', name)
            end
        end
    end
    return index
end

function packs.lookup(index, speaker, text)
    return index.by_key[key(speaker, text)]
end

-- Why a line was missed: the same text spoken by other names, and lines
-- that fold to the same letters (a punctuation or glyph difference).
function packs.diagnose(index, speaker, text, limit)
    limit = limit or 3
    local speakers = {}
    for _, s in ipairs(index.by_text[text] or {}) do
        if s ~= speaker then speakers[#speakers + 1] = s end
    end
    local near = {}
    for _, row in ipairs(index.by_fold[key(fold(speaker), fold(text))] or {}) do
        if (row.speaker ~= speaker or row.text ~= text) and #near < limit then near[#near + 1] = row end
    end
    return {speakers_with_text = speakers, near = near}
end

-- "rhapsodies 0.1 (1193 lines)" per pack, or a note that nothing is installed
function packs.describe(index)
    if #index.packs == 0 then return {'no audio packs installed under ' .. index.root} end
    local out = {}
    for _, p in ipairs(index.packs) do
        out[#out + 1] = ('%s %s (%d lines)'):format(p.name, p.version, p.lines)
    end
    return out
end

return packs
