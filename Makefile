# Install Spoken into Windower as a symlink to this checkout, so edits here
# are live in game after //lua reload spoken.
#
#   make install     symlink this repo into Windower's addons folder
#   make uninstall   remove that symlink
#   make where       print the Windower folder that was found
#   make test        run the tests under luajit
#   make lint        run luacheck
#   make check       both
#   make helper      run the host-side TTS helper against ./data
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

.PHONY: install uninstall where test lint check helper

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
	python3 -I helper/spoken_helper.py --data $(REPO)/data

lint:
	luacheck .

check: lint test
