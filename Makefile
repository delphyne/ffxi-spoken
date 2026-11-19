# Install Spoken into Windower as a symlink to this checkout, so edits here
# are live in game after //lua reload spoken.
#
#   make install     symlink this repo into Windower's addons folder
#   make uninstall   remove that symlink
#   make where       print the Windower folder that was found
#   make test        run the tests under luajit
#   make lint        run luacheck
#   make check       both
#   make deps        cross-build libogg, libopus, and opusfile (32-bit Windows, static) into native/deps/
#   make native      build libs/spokenplay.dll, the audio player (needs i686-w64-mingw32-gcc and make deps)
#   make native-test FILE="audio/<pack>/<ver>/<Speaker>/<file>.opus" [STOP_MS=1500] [VOLUME=50]
#                    play one file through the same code under the game's Wine prefix
#   make venv        build .venv (Python 3.12 via uv) with the TTS engines and PyYAML
#   make venv-cuda   add the CUDA build of onnxruntime so Kokoro runs on an NVIDIA GPU
#   make voices      download the Kokoro model and a few Piper voices
#   make census      rebuild helper/census.json from a LandSandBoat checkout (LSB=path)
#   make nouns [MISSIONS=rov] [ONLY=1]
#                    rank the proper nouns in the game's dialogue for the lexicon (data/),
#                    a storyline's words first; ONLY=1 keeps just the words its cutscenes speak
#                    (ROV=1 is the old spelling of MISSIONS=rov ONLY=1)
#   make review-lexicon   go through them, listening, and build helper/lexicon.yaml: a window
#                    with a table of every word, new and in the lexicon, filtered by status,
#                    storyline, and speaker (CLI=1 for the terminal loop instead; ENTRIES=1
#                    reviews the existing entries, ENTRY=Word starts at one)
#   make pack VERSION=0.1 [PACK=rhapsodies] [MISSIONS=rov | SOURCE="quests/jeuno ..."] [LIMIT=N] [DRY=1] [FORCE=1] [BACKEND=moss]
#                    (SOURCE names tools/corpus.py sources in place of a mission folder; a line of
#                    dialogue with no speaker stops the build, and DRY=1 says where they are)
#                    (BACKEND=moss runs under .venv-moss; MOSS_TEMPERATURE, MOSS_N_VQ, MOSS_INT8,
#                    MOSS_CODEC=cpu32|gpu16 tune it, see helper/moss.py)
#                    build an audio pack: audio/<pack>/<version>/, the zip beside it,
#                    and packs/<pack>/<version>/manifest.json plus packs/index.json to commit
#   make release VERSION=0.1 [PACK=rhapsodies] [GH_REPO=owner/name]
#                    upload that zip as a GitHub Release (tag <pack>-<version>) with gh
#   make helper      run the host-side TTS helper against ./data
#                    (make helper BACKEND=piper to pick an engine)
#   make say TEXT="..." [SPEAKER=Iroha] [RAW=1] [FRESH=1]   speak one line once; FRESH ignores the cache
#   make venv-qwen   build .venv-qwen (Python 3.12) with qwen-tts (torch, CUDA) and kokoro-onnx
#   make spike-qwen [LIMIT=N] [RAW=1] [FRESH=1]
#                    render a fixed set of Rhapsodies lines through Kokoro and Qwen3-TTS into
#                    data/spike/listen.html, to judge a Qwen3 backend by ear (tools/spike_qwen3.py)
#   make elevenlabs-key   store the ElevenLabs API key in the keyring (macOS Keychain or secret-tool); the
#                    keyring prompts for it, unseen. ELEVENLABS_API_KEY overrides it (helper/elevenlabs.py)
#   make elevenlabs-check   read the account, spending nothing: plan, characters left, voice slots, voices,
#                    models; says which permissions a restricted key still lacks
#   make voice-prompts [ONLY=Name] [QUALITY=high]
#                    print each own voice's prompt from helper/voice_designs.yaml in the form ElevenLabs
#                    asks for, to paste into its voice design. QUALITY is the level the prompt asks for
#                    ("High quality."); it also goes to the four targets below, which compose the same prompt
#   make design-voices [ONLY=Name] [CHANGED=1] [FRESH=1] [ADOPT=dir]
#                    turn helper/voice_designs.yaml into reference clips with Qwen3 VoiceDesign
#                    (helper/voices/moss/refs/); voices with no clip, plus changed ones with CHANGED
#   make audit-voices [ONLY=Name] [CHANGED=1] [FRESH=1]
#                    which voices lack a clip, which a design-voices run with those flags would
#                    design and why, which are stale but kept, and which are current
#   make listen-voices   a page with every designed voice and its description, to listen to
#   make review-voices   a window to edit the voices, grouped by race: each pool voice (shared by the
#                    speakers hashed onto it) and each own voice (with its accent, persona, and emotion);
#                    see and copy the prompt, audition, regenerate, accept, or reset, filtered by race, gender, storyline, or a partial name, and hear a
#                    voice say one of its speaker's lines through MOSS (needs .venv-moss) (tools/voice_review.py)
#   make voice-stories   rebuild helper/voice_stories.json: each speaker's lines per storyline, and a few to hear
#   make corpus [SOURCE=missions/rov]
#                    extract what a source says (a mission folder, a quest region, a companion; all of
#                    them without SOURCE), each line classed: the text into data/corpus/, the counts
#                    into helper/corpus/ (tools/corpus.py; helper/exclude.yaml is what is never voiced)
#   make audit-corpus SOURCE=quests/jeuno [SHOW=narration] [SPEAKER=Name] [SAMPLES=12]
#                    what each exclude rule dropped, and samples of what stayed
#   make venv-moss   build .venv-moss with MOSS-TTS from a checkout (MOSS_DIR=~/scm/github/OpenMOSS/MOSS-TTS)
#   make spike-moss [LIMIT=N] [FRESH=1] [INT8=1]
#                    the same lines through MOSS-TTS-Local, cloned from the Qwen-designed clips, with
#                    the lexicon's IPA; adds columns to the same page (tools/spike_moss.py)
#
# The Windower folder is found automatically: the Lumoria flatpak prefix on
# Linux, else a plain Wine prefix. Set WINDOWER to point somewhere else:
#
#   make install WINDOWER=/path/to/Windower4

REPO := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
NAME := Spoken

# Candidate Windower folders, first match wins.
CANDIDATES := \
	$(wildcard $(HOME)/.var/app/net.windower.Lumoria/data/prefixes/*/pfx/drive_c/Windower4) \
	$(wildcard $(HOME)/.wine/drive_c/Windower4) \
	$(wildcard $(HOME)/.wine/drive_c/Windower)

WINDOWER ?= $(firstword $(CANDIDATES))
ADDONS := $(WINDOWER)/addons
LINK := $(ADDONS)/$(NAME)

# The TTS engines need onnxruntime, which has no wheel for the system
# Python yet, so they live in a Python 3.12 venv that uv builds. The helper
# runs with the venv's python when it exists and the system python3 otherwise
# (espeak-ng or dry run only).
UV ?= $(firstword $(wildcard $(HOME)/.local/bin/uv) $(shell command -v uv 2>/dev/null))
VENV := $(REPO)/.venv
VENV_QWEN := $(REPO)/.venv-qwen
VENV_MOSS := $(REPO)/.venv-moss
MOSS_DIR ?= $(HOME)/scm/github/OpenMOSS/MOSS-TTS
PY := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
VOICES := $(REPO)/helper/voices
PIPER_VOICES ?= en_US-lessac-medium en_US-amy-medium en_GB-alan-medium
KOKORO_URL := https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
BACKEND ?= auto
# the quality level a voice's prompt asks for: "<Quality> quality." (helper/designs.py)
QUALITY ?= high
# The moss backend lives in its own venv (torch 2.9.1, transformers 5) with its
# models under helper/voices/moss/hf; every other backend uses .venv.
ENGINE_PY := $(if $(filter moss,$(BACKEND)),HF_HOME="$(VOICES)/moss/hf" PYTORCH_ALLOC_CONF=expandable_segments:True $(VENV_MOSS)/bin/python,$(PY))

# onnxruntime-gpu 1.32 is built against CUDA 13; these pip packages supply the
# libraries and onnxruntime.preload_dlls() finds them, so no system CUDA is
# needed. The driver must support CUDA 13 (nvidia-smi shows the version).
CUDA_PKGS := onnxruntime-gpu "nvidia-cuda-runtime>=13,<14" nvidia-cudnn-cu13 "nvidia-cublas>=13,<14" \
	"nvidia-cufft>=12" "nvidia-curand>=10" "nvidia-cuda-nvrtc>=13,<14" "nvidia-nvjitlink>=13,<14"

LSB ?= $(HOME)/scm/github/LandSandBoat/server

# The player is a 32-bit Windows DLL linked against Windower's LuaCore.dll,
# like Balloon's gamepad module. Any Lua 5.1 compatible headers will do;
# LuaJIT's are 5.1. Opus decoding is static: make deps builds the libraries.
CC32 ?= i686-w64-mingw32-gcc
LUA_INC ?= $(firstword $(wildcard \
	/home/linuxbrew/.linuxbrew/include/luajit-2.1 \
	/usr/include/luajit-2.1 \
	/usr/include/lua5.1 \
	/usr/include/lua-5.1))
LUACORE := $(WINDOWER)/plugins/LuaCore.dll
DEPS := $(REPO)/native/deps/prefix
DEP_LIBS := $(DEPS)/lib/libopusfile.a $(DEPS)/lib/libopus.a $(DEPS)/lib/libogg.a
NATIVE_CFLAGS := -O2 -Wall -Wextra -D_WIN32_WINNT=0x0600 -I$(DEPS)/include -I$(DEPS)/include/opus
# the Wine prefix two levels above drive_c/Windower4, for running the test exe
WINEPREFIX := $(abspath $(WINDOWER)/../..)

.PHONY: install uninstall where test lint check helper say venv venv-cuda venv-qwen spike-qwen elevenlabs-key elevenlabs-check voice-prompts design-voices audit-voices listen-voices review-voices voice-stories corpus audit-corpus venv-moss spike-moss voices census nouns review-lexicon pack release deps native native-test

# Each recipe below is one shell command, so an exit inside it ends the target.

where:
	@if [ -z "$(WINDOWER)" ]; then \
		echo "No Windower install found. Set WINDOWER=/path/to/Windower4." >&2; exit 1; \
	fi; \
	echo "$(WINDOWER)"

install:
	@if [ -z "$(WINDOWER)" ]; then \
		echo "No Windower install found. Set WINDOWER=/path/to/Windower4." >&2; exit 1; \
	fi; \
	if [ ! -d "$(ADDONS)" ]; then \
		echo "$(ADDONS) is not a directory." >&2; exit 1; \
	fi; \
	if [ -L "$(LINK)" ]; then \
		if [ "$$(readlink -f "$(LINK)")" = "$(REPO)" ]; then \
			echo "$(LINK) already points here."; exit 0; \
		fi; \
		echo "Replacing symlink $(LINK) -> $$(readlink "$(LINK)")"; \
		unlink "$(LINK)"; \
	elif [ -e "$(LINK)" ]; then \
		echo "$(LINK) exists and is not a symlink." >&2; \
		echo "Move it aside (keep its data/settings.xml if you want it), then rerun." >&2; \
		exit 1; \
	fi; \
	ln -s "$(REPO)" "$(LINK)" && \
	echo "Installed $(LINK) -> $(REPO)" && \
	echo "In game: //lua load $(NAME)"

uninstall:
	@if [ -z "$(WINDOWER)" ]; then \
		echo "No Windower install found. Set WINDOWER=/path/to/Windower4." >&2; exit 1; \
	fi; \
	if [ -L "$(LINK)" ]; then unlink "$(LINK)" && echo "Removed $(LINK)"; \
	elif [ -e "$(LINK)" ]; then echo "$(LINK) is not a symlink; leaving it." >&2; exit 1; \
	else echo "Nothing installed at $(LINK)."; fi

# The system python3 when it has PyYAML (Fedora's does), else the venv's
# (Homebrew's python3 has none and will not take one).
TEST_PY ?= $(shell python3 -c 'import yaml' 2>/dev/null && echo python3 || echo "$(PY)")
test:
	luajit test/test_$(NAME).lua
	$(TEST_PY) -I -m unittest discover -s helper -p 'test_*.py'
	$(TEST_PY) -I -m unittest discover -s tools -p 'test_*.py'

# Run the host-side helper against this checkout's data folder.
helper:
	$(PY) -I helper/spoken_helper.py --data $(REPO)/data --backend $(BACKEND)

say:
	@if [ -z "$(TEXT)" ]; then echo 'usage: make say TEXT="Welcome to Vana'"'"'diel." [SPEAKER=Iroha] [RAW=1]' >&2; exit 1; fi; \
	$(ENGINE_PY) -I helper/spoken_helper.py --backend $(BACKEND) --say "$(TEXT)" --speaker "$(SPEAKER)" $(if $(RAW),--no-lexicon) $(if $(FRESH),--fresh)

venv:
	@if [ -z "$(UV)" ]; then \
		echo "uv not found; install it with: python3 -m pip install --user uv" >&2; exit 1; \
	fi; \
	[ -x "$(VENV)/bin/python" ] || "$(UV)" venv "$(VENV)" --python 3.12; \
	"$(UV)" pip install --python "$(VENV)/bin/python" piper-tts kokoro-onnx pyyaml

# System python3 (Fedora ships PyYAML); the venv works too once built.
census:
	python3 -I helper/census.py --lsb "$(LSB)" --client --report

# Reads the client's dialogue DATs and decodes one storyline's scenes
# (MISSIONS, the same folder name `make pack` takes); Kokoro IPA needs the
# venv. ONLY=1 limits the list to that pack's words.
nouns data/proper_nouns.tsv:
	$(PY) -I tools/proper_nouns.py --lsb "$(LSB)" --missions $(if $(ROV),rov,$(MISSIONS)) $(if $(or $(ROV),$(ONLY)),--only)

# One file per unique speaker and line, Opus, with a manifest; the WAV cache
# in helper/cache makes a rebuild cost only what changed.
# The audio itself is never committed (audio/ is ignored): the manifest and
# index are, and `make release` puts the zip on a GitHub Release whose URL the
# index already names. GH_REPO is the owner/name that URL points at.
PACK ?= rhapsodies
MISSIONS ?= rov
GH_REPO ?= delphyne/ffxi-spoken
pack:
	@if [ -z "$(VERSION)" ]; then echo 'usage: make pack VERSION=0.1 [PACK=rhapsodies] [MISSIONS=rov] [LIMIT=N] [DRY=1] [FORCE=1]' >&2; exit 1; fi; \
	$(ENGINE_PY) -I tools/build_pack.py $(PACK) $(VERSION) --missions $(MISSIONS) $(foreach s,$(SOURCE),--source $(s)) --backend $(BACKEND) --lsb "$(LSB)" \
		--repo $(GH_REPO) $(if $(LIMIT),--limit $(LIMIT)) $(if $(DRY),--dry-run) $(if $(FORCE),--force)

release:
	@if [ -z "$(VERSION)" ]; then echo 'usage: make release VERSION=0.1 [PACK=rhapsodies] [GH_REPO=owner/name]' >&2; exit 1; fi; \
	test -f audio/$(PACK)-$(VERSION).zip || { echo "audio/$(PACK)-$(VERSION).zip is missing; run make pack first" >&2; exit 1; }; \
	$(PY) -I tools/build_pack.py $(PACK) $(VERSION) --notes > audio/$(PACK)-$(VERSION).notes && \
	gh release create $(PACK)-$(VERSION) audio/$(PACK)-$(VERSION).zip \
		$(wildcard audio/$(PACK)-$(VERSION)-voices.zip) --repo $(GH_REPO) \
		--title "$(PACK) $(VERSION)" --notes-file audio/$(PACK)-$(VERSION).notes

# Interactive: a accept, r repeat, j reject and type IPA, s skip, e example, q quit;
# a Tk window with buttons and an IPA palette when a display is there, else the
# terminal. Resumes where it left off (data/review_progress.json).
review-lexicon: data/proper_nouns.tsv data/word_stories.json
	$(PY) -I helper/review.py --backend $(BACKEND) $(if $(CLI),--cli) $(if $(ENTRIES),--entries) $(if $(ENTRY),--entry "$(ENTRY)")

venv-cuda: venv
	"$(UV)" pip uninstall --python "$(VENV)/bin/python" onnxruntime onnxruntime-gpu
	"$(UV)" pip install --python "$(VENV)/bin/python" $(CUDA_PKGS)
	"$(VENV)/bin/python" -c "import onnxruntime as o; o.preload_dlls(); print(o.get_available_providers())"

# Qwen3-TTS needs torch, which is several GB and brings its own CUDA runtime,
# so it gets a venv of its own; deleting .venv-qwen undoes the experiment.
# Kokoro goes in too (onnxruntime-gpu and the same CUDA 13 wheels as venv-cuda)
# so one interpreter renders both sides. The models (two 1.7B checkpoints,
# about 3.4 GB each) download from Hugging Face into helper/voices/qwen3/hf
# the first time the spike runs. torch and torchaudio must be the same
# release (qwen-tts pins neither, and a mismatched torchaudio fails to load
# its C library); the CUDA 12.8 wheels on PyPI still cover Turing cards.
# qwen-tts also pulls the CPU onnxruntime, which shares files with the GPU
# build, so both come out and the GPU one goes back in, as venv-cuda does.
QWEN_TORCH := torch==2.10.0 torchaudio==2.10.0
venv-qwen:
	@if [ -z "$(UV)" ]; then \
		echo "uv not found; install it with: python3 -m pip install --user uv" >&2; exit 1; \
	fi; \
	[ -x "$(VENV_QWEN)/bin/python" ] || "$(UV)" venv "$(VENV_QWEN)" --python 3.12; \
	"$(UV)" pip install --python "$(VENV_QWEN)/bin/python" $(QWEN_TORCH) qwen-tts soundfile pyyaml kokoro-onnx && \
	"$(UV)" pip uninstall --python "$(VENV_QWEN)/bin/python" onnxruntime onnxruntime-gpu && \
	"$(UV)" pip install --python "$(VENV_QWEN)/bin/python" $(CUDA_PKGS) && \
	"$(VENV_QWEN)/bin/python" -c "import torch, torchaudio; print('torch', torch.__version__, torchaudio.__version__, 'cuda', torch.cuda.is_available())"

spike-qwen:
	@test -x "$(VENV_QWEN)/bin/python" || { echo ".venv-qwen is missing; run make venv-qwen" >&2; exit 1; }; \
	HF_HOME="$(VOICES)/qwen3/hf" PYTORCH_ALLOC_CONF=expandable_segments:True \
		"$(VENV_QWEN)/bin/python" -I tools/spike_qwen3.py \
		$(if $(LIMIT),--limit $(LIMIT)) $(if $(RAW),--also-raw) $(if $(FRESH),--fresh)

# Standard library only, so the system python3 will do (docs/ElevenLabs-Handoff.md).
elevenlabs-key:
	@python3 -I helper/elevenlabs.py key

elevenlabs-check:
	@python3 -I helper/elevenlabs.py check

# The prompts ElevenLabs' voice design is given by hand (docs/ElevenLabs-Handoff.md).
voice-prompts:
	@$(PY) -I tools/design_voices.py --prompts --quality "$(QUALITY)" $(if $(ONLY),--only "$(ONLY)")

# Reference clips for the MOSS backend (docs/MOSS-Backend-Handoff.md), designed
# by Qwen3 in .venv-qwen. ADOPT=data/spike/ref keeps clips already heard.
design-voices:
	@test -x "$(VENV_QWEN)/bin/python" || { echo ".venv-qwen is missing; run make venv-qwen" >&2; exit 1; }; \
	HF_HOME="$(VOICES)/qwen3/hf" PYTORCH_ALLOC_CONF=expandable_segments:True \
		"$(VENV_QWEN)/bin/python" -I tools/design_voices.py --quality "$(QUALITY)" \
		$(if $(ONLY),--only "$(ONLY)") $(if $(CHANGED),--changed) $(if $(FRESH),--fresh) $(if $(ADOPT),--adopt "$(ADOPT)")

audit-voices:
	$(PY) -I tools/design_voices.py --audit --quality "$(QUALITY)" $(if $(ONLY),--only "$(ONLY)") $(if $(CHANGED),--changed) $(if $(FRESH),--fresh)

listen-voices:
	$(PY) -I tools/design_voices.py --review --quality "$(QUALITY)"

review-voices:
	@test -x "$(VENV_QWEN)/bin/python" || { echo ".venv-qwen is missing; run make venv-qwen" >&2; exit 1; }; \
	HF_HOME="$(VOICES)/qwen3/hf" PYTORCH_ALLOC_CONF=expandable_segments:True \
		"$(VENV_QWEN)/bin/python" -I tools/voice_review.py --quality "$(QUALITY)"

# Needs the client and a LandSandBoat checkout, like make pack; system python3.
# Writes helper/voice_stories.json and data/word_stories.json (for review-lexicon).
voice-stories data/word_stories.json:
	python3 -I tools/voice_stories.py --lsb "$(LSB)"

# The same needs. Without SOURCE every source is extracted and the exclude
# rules that matched nothing are named.
corpus:
	python3 -I tools/corpus.py --lsb "$(LSB)" extract $(if $(SOURCE),$(SOURCE),--all --stale)

audit-corpus:
	@if [ -z "$(SOURCE)" ]; then echo "usage: make audit-corpus SOURCE=quests/jeuno [SHOW=narration] [SPEAKER=Name]" >&2; exit 1; fi
	python3 -I tools/corpus.py --lsb "$(LSB)" audit $(SOURCE) $(if $(SHOW),--show $(SHOW)) \
		$(if $(SPEAKER),--speaker "$(SPEAKER)") $(if $(SAMPLES),--samples $(SAMPLES))

# MOSS-TTS is installed from its checkout (pip -e) because it is not on PyPI;
# it pins torch 2.9.1+cu128, so it cannot share .venv-qwen. The model
# (OpenMOSS-Team/MOSS-TTS-Local-Transformer, about 6 GB) downloads into
# helper/voices/moss/hf on the first run.
venv-moss:
	@if [ -z "$(UV)" ]; then \
		echo "uv not found; install it with: python3 -m pip install --user uv" >&2; exit 1; \
	fi; \
	[ -d "$(MOSS_DIR)" ] || git clone --depth 1 https://github.com/OpenMOSS/MOSS-TTS.git "$(MOSS_DIR)"; \
	[ -x "$(VENV_MOSS)/bin/python" ] || "$(UV)" venv "$(VENV_MOSS)" --python 3.12; \
	"$(UV)" pip install --python "$(VENV_MOSS)/bin/python" --torch-backend cu128 -e "$(MOSS_DIR)[torch-runtime]" soundfile pyyaml bitsandbytes && \
	"$(VENV_MOSS)/bin/python" -c "import torch, transformers; print('torch', torch.__version__, 'transformers', transformers.__version__, 'cuda', torch.cuda.is_available())"

spike-moss:
	@test -x "$(VENV_MOSS)/bin/python" || { echo ".venv-moss is missing; run make venv-moss" >&2; exit 1; }; \
	HF_HOME="$(VOICES)/moss/hf" PYTORCH_ALLOC_CONF=expandable_segments:True \
		"$(VENV_MOSS)/bin/python" -I tools/spike_moss.py \
		$(if $(LIMIT),--limit $(LIMIT)) $(if $(FRESH),--fresh) $(if $(INT8),--int8)

voices: venv
	mkdir -p "$(VOICES)/piper" "$(VOICES)/kokoro"
	"$(VENV)/bin/python" -m piper.download_voices --download-dir "$(VOICES)/piper" $(PIPER_VOICES)
	@for f in kokoro-v1.0.onnx voices-v1.0.bin; do \
		if [ ! -s "$(VOICES)/kokoro/$$f" ]; then \
			echo "downloading $$f"; curl -sSL -o "$(VOICES)/kokoro/$$f" "$(KOKORO_URL)/$$f" || exit 1; \
		fi; \
	done

lint:
	luacheck .

check: lint test

deps $(DEP_LIBS):
	native/build-deps.sh

native: libs/spokenplay.dll

libs/spokenplay.dll: native/spokenplay_lua.c native/spokenplay_core.c native/spokenplay.h $(DEP_LIBS)
	@if [ -z "$(LUA_INC)" ]; then echo "No Lua 5.1 headers found; set LUA_INC." >&2; exit 1; fi; \
	if [ ! -f "$(LUACORE)" ]; then echo "$(LUACORE) not found; set WINDOWER." >&2; exit 1; fi; \
	mkdir -p libs && \
	$(CC32) $(NATIVE_CFLAGS) -shared -static-libgcc -o $@.tmp native/spokenplay_lua.c native/spokenplay_core.c \
		-I"$(LUA_INC)" $(DEP_LIBS) "$(LUACORE)" -lwinmm && \
	echo "imports:" && $(CC32:gcc=objdump) -p $@.tmp | grep 'DLL Name' && \
	mv -f $@.tmp $@ && \
	echo "Built $@ (//lua unload spoken before replacing it, then //lua load spoken)"

native/spokenplay_test.exe: native/spokenplay_test.c native/spokenplay_core.c native/spokenplay.h $(DEP_LIBS)
	$(CC32) $(NATIVE_CFLAGS) -static-libgcc -o $@ native/spokenplay_test.c native/spokenplay_core.c $(DEP_LIBS) -lwinmm

# Windows APIs take forward slashes, so Z:/unix/path works as a path here.
native-test: native/spokenplay_test.exe
	@if [ -z "$(FILE)" ]; then echo 'usage: make native-test FILE="audio/rhapsodies/0.1/Iroha/<file>.opus" [STOP_MS=1500] [VOLUME=50]' >&2; exit 1; fi; \
	flatpak run --env=WINEPREFIX="$(WINEPREFIX)" --env=WINEDEBUG=-all --command=wine net.windower.Lumoria \
		"Z:$(REPO)/native/spokenplay_test.exe" "Z:$$(realpath "$(FILE)")" $(STOP_MS) $(VOLUME)
