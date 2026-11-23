COUNTRY_CODE=MY
GEONAMES_URL=https://download.geonames.org/export/dump/$(COUNTRY_CODE).zip

VENV_DIR := .venv
VENV_SENTINEL := $(VENV_DIR)/venv_sentinel

ifeq ($(OS),Windows_NT)
SYSTEM_PYTHON ?= python
PYTHON := $(VENV_DIR)/Scripts/python.exe
else
SYSTEM_PYTHON ?= python3
PYTHON := $(VENV_DIR)/bin/python
endif

echo:
	echo $(SYSTEM_PYTHON)
	echo $(PYTHON)
	
downloads/$(COUNTRY_CODE).zip: | $(VENV_SENTINEL)
	mkdir -p downloads
	wget -O $@ $(GEONAMES_URL)

data/$(COUNTRY_CODE).txt: downloads/$(COUNTRY_CODE).zip | $(VENV_SENTINEL)
	mkdir -p data
	unzip -p $< $(COUNTRY_CODE).txt > $@

$(VENV_SENTINEL): requirements.txt
	$(SYSTEM_PYTHON) -m venv $(VENV_DIR)
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -c "from pathlib import Path; Path(r'$@').touch()"

install: $(VENV_SENTINEL)

# data/geonames.db: data/$(COUNTRY_CODE).txt | $(VENV_SENTINEL)
# 	# Create the SQLite database file
# 	mkdir -p data
# 	$(PYTHON) -m sqlite_utils create-database $@
# 	# Use the create table statement in resources/create_table.sql to create the 'geonames' table in the SQLite database
# 	$(PYTHON) -m sqlite_utils query $@ - < resources/create_table_geonames.sql
# 	# Insert the data from the text file into the 'geonames' table
# 	$(PYTHON) -m sqlite_utils insert $@ geonames data/$(COUNTRY_CODE).txt --tsv --pk=geonameid --no-headers

data/geonames.db: data/$(COUNTRY_CODE).txt | $(VENV_SENTINEL)
	mkdir -p data
	$(PYTHON) -m sqlite_utils create-database $@
	$(PYTHON) -m sqlite_utils query $@ - < resources/create_table_geonames.sql
	{ $(PYTHON) -c 'print("\t".join(["geonameid","name","asciiname","alternatenames","latitude","longitude","feature_class","feature_code","country_code","cc2","admin1_code","admin2_code","admin3_code","admin4_code","population","elevation","dem","timezone","modification_date"]))'; cat data/$(COUNTRY_CODE).txt; } | $(PYTHON) -m sqlite_utils insert $@ geonames - --tsv --pk=geonameid
	
run: data/geonames.db | $(VENV_SENTINEL)
	$(PYTHON) -m datasette serve $< --host 0.0.0.0 --port 8001 --plugins-dir plugins

clean:
	rm -rf data

sterilise: clean
	rm -rf downloads