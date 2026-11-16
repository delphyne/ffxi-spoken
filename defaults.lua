-- User settings. Windower persists these to data/settings.xml.
return {
    -- speak NPC dialogue (modes 150 and 144)
    Enabled = true,
    -- 'audio' plays the installed packs under audio/ through the native player;
    -- 'helper' queues lines in data/ for the developer's helper process
    Mode = 'audio',
    -- also speak system narration (mode 151)
    System = false,
    -- send Enter when a prompted line has finished playing
    AutoAdvance = false,
    -- playback volume in audio mode, 0 to 100
    Volume = 100,
    -- write every line no pack has to data/misses.jsonl, with where it happened
    Log = false,
    -- how often, in frames, to ask the player or the helper how the line is going
    PollFrames = 6,
}
