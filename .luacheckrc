std = 'luajit'
-- Windower's addon environment: _addon/_libs/windower are injected by the
-- host, the rest come from require('luau') and the chat libs.
globals = {
    '_addon', '_libs', 'windower', 'S', 'L', 'T', 'config', 'log', 'notice',
    'res', 'chars',
    -- luau extends the standard libraries too
    string = {fields = {'trim', 'endswith', 'startswith', 'split'}},
    coroutine = {fields = {'sleep', 'close'}},
}
-- Spoken.lua defines its handlers as top-level globals.
allow_defined_top = true
unused_args = false
max_line_length = false
-- the harness swaps io.open for an in-memory version
files['test'] = {globals = {'package', io = {fields = {'open'}}}}
