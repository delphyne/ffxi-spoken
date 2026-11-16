# Install Spoken into Windower as a symlink to this checkout, so edits here
# are live in game after //lua reload spoken.
#
#   make install     symlink this repo into Windower's addons folder
#   make uninstall   remove that symlink
#   make where       print the Windower folder that was found
#   make test        run the tests under luajit
#   make lint        run luacheck
#   make check       both
#   make venv        build .venv (Python 3.12 via uv) with the TTS engines and PyYAML
#   make venv-cuda   add the CUDA build of onnxruntime so Kokoro runs on an NVIDIA GPU
#   make voices      download the Kokoro model and a few Piper voices
#   make census      rebuild helper/census.json from a LandSandBoat checkout (LSB=path)
#   make nouns       rank the proper nouns in the game's dialogue for the lexicon (data/)
#   make review      go through them one by one, listening, and build helper/lexicon.yaml
#   make helper      run the host-side TTS helper against ./data
#                    (make helper BACKEND=piper to pick an engine)
#   make say TEXT="..." [SPEAKER=Iroha] [RAW=1] [FRESH=1]   speak one line once; FRESH ignores the cache
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
PY := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
VOICES := $(REPO)/helper/voices
PIPER_VOICES ?= en_US-lessac-medium en_US-amy-medium en_GB-alan-medium
KOKORO_URL := https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
BACKEND ?= auto

# onnxruntime-gpu 1.32 is built against CUDA 13; these pip packages supply the
# libraries and onnxruntime.preload_dlls() finds them, so no system CUDA is
# needed. The driver must support CUDA 13 (nvidia-smi shows the version).
CUDA_PKGS := onnxruntime-gpu "nvidia-cuda-runtime>=13,<14" nvidia-cudnn-cu13 "nvidia-cublas>=13,<14" \
	"nvidia-cufft>=12" "nvidia-curand>=10" "nvidia-cuda-nvrtc>=13,<14" "nvidia-nvjitlink>=13,<14"

LSB ?= $(HOME)/scm/github/LandSandBoat/server

.PHONY: install uninstall where test lint check helper say venv venv-cuda voices census nouns review

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

test:
	luajit test/test_$(NAME).lua
	python3 -I -m unittest discover -s helper -p 'test_*.py'

# Run the host-side helper against this checkout's data folder.
helper:
	$(PY) -I helper/spoken_helper.py --data $(REPO)/data --backend $(BACKEND)

say:
	@if [ -z "$(TEXT)" ]; then echo 'usage: make say TEXT="Welcome to Vana'"'"'diel." [SPEAKER=Iroha] [RAW=1]' >&2; exit 1; fi; \
	$(PY) -I helper/spoken_helper.py --backend $(BACKEND) --say "$(TEXT)" --speaker "$(SPEAKER)" $(if $(RAW),--no-lexicon) $(if $(FRESH),--fresh)

venv:
	@if [ -z "$(UV)" ]; then \
		echo "uv not found; install it with: python3 -m pip install --user uv" >&2; exit 1; \
	fi; \
	[ -x "$(VENV)/bin/python" ] || "$(UV)" venv "$(VENV)" --python 3.12; \
	"$(UV)" pip install --python "$(VENV)/bin/python" piper-tts kokoro-onnx pyyaml

# System python3 (Fedora ships PyYAML); the venv works too once built.
census:
	python3 -I helper/census.py --lsb "$(LSB)" --client --report

# Reads the client's dialogue DATs; Kokoro IPA needs the venv.
nouns data/proper_nouns.tsv:
	$(PY) -I tools/proper_nouns.py --lsb "$(LSB)"

# Interactive: a accept, r repeat, j reject and type IPA, s skip, e example, q quit.
# Resumes where it left off (data/review_progress.json).
review: data/proper_nouns.tsv
	$(PY) -I helper/review.py --backend $(BACKEND)

venv-cuda: venv
	"$(UV)" pip uninstall --python "$(VENV)/bin/python" onnxruntime onnxruntime-gpu
	"$(UV)" pip install --python "$(VENV)/bin/python" $(CUDA_PKGS)
	"$(VENV)/bin/python" -c "import onnxruntime as o; o.preload_dlls(); print(o.get_available_providers())"

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
