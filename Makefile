COUNTRY_CODE_2=MY
COUNTRY_CODE_3=MYS
COUNTRY_CODE=${COUNTRY_CODE_2}

GEONAMES_COUNTRYDATA_URL=https://download.geonames.org/export/dump/$(COUNTRY_CODE).zip
GEONAMES_ADMIN1_CODES_URL=https://download.geonames.org/export/dump/admin1CodesASCII.txt
GEONAMES_ADMIN2_CODES_URL=https://download.geonames.org/export/dump/admin2Codes.txt
GADM_GPKG_URL=https://geodata.ucdavis.edu/gadm/gadm4.1/gpkg/gadm41_$(COUNTRY_CODE_3).gpkg

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
	wget -O $@ $(GEONAMES_COUNTRYDATA_URL)

downloads/$(COUNTRY_CODE).gpkg: | $(VENV_SENTINEL)
	mkdir -p downloads
	wget -O $@ $(GADM_GPKG_URL)

data/$(COUNTRY_CODE).txt: downloads/$(COUNTRY_CODE).zip | $(VENV_SENTINEL)
	mkdir -p data
	unzip -p $< $(COUNTRY_CODE).txt > $@

downloads/admin_1_codes.txt: | $(VENV_SENTINEL)
	mkdir -p downloads
	wget -O $@ $(GEONAMES_ADMIN1_CODES_URL)

data/admin_1_codes.csv: downloads/admin_1_codes.txt
	mkdir -p data
	# filter for lines starting with the 2 letter country code
	grep "^$(COUNTRY_CODE_2)\." $^ > $@.filtered
	cut -c 4- $@.filtered > $@
	rm $@.filtered
	# Insert header row into the tab separated file
	cp resources/admin_codes_headers.txt $@.hdrs
	# write eol to the header file
	echo "" >> $@.hdrs
	cat $@ >> $@.hdrs
	mv $@.hdrs $@

downloads/admin_2_codes.txt: | $(VENV_SENTINEL)
	mkdir -p downloads
	wget -O $@ $(GEONAMES_ADMIN2_CODES_URL)

data/admin_2_codes.csv: downloads/admin_2_codes.txt
	mkdir -p data
	# filter for lines starting with the 2 letter country code
	grep "^$(COUNTRY_CODE_2)\." $^ > $@.filtered
	cut -c 7- $@.filtered > $@
	rm $@.filtered
	# Insert header row into the tab separated file
	cp resources/admin_codes_headers.txt $@.hdrs
	# write eol to the header file
	echo "" >> $@.hdrs
	cat $@ >> $@.hdrs
	mv $@.hdrs $@

admin: data/admin_1_codes.csv data/admin_2_codes.csv

$(VENV_SENTINEL): requirements.txt
	$(SYSTEM_PYTHON) -m venv $(VENV_DIR)
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -c "from pathlib import Path; Path(r'$@').touch()"

install: $(VENV_SENTINEL)

data/geonames.db: data/$(COUNTRY_CODE).txt data/admin_1_codes.csv data/admin_2_codes.csv | $(VENV_SENTINEL)
	mkdir -p data
	$(PYTHON) -m sqlite_utils create-database $@
	$(PYTHON) -m sqlite_utils query $@ - < resources/create_table_feature_classes.sql
	$(PYTHON) -m sqlite_utils insert $@ feature_classes resources/feature_classes.csv --csv --pk=code
	$(PYTHON) -m sqlite_utils query $@ - < resources/create_table_feature_codes.sql
	$(PYTHON) -m sqlite_utils insert $@ feature_codes resources/feature_codes.csv --csv --pk=code
	$(PYTHON) -m sqlite_utils query $@ - < resources/create_table_admin_1_codes.sql
	$(PYTHON) -m sqlite_utils insert $@ admin_1_codes data/admin_1_codes.csv --tsv --pk=code
	$(PYTHON) -m sqlite_utils query $@ - < resources/create_table_admin_2_codes.sql
	$(PYTHON) -m sqlite_utils insert $@ admin_2_codes data/admin_2_codes.csv --tsv --pk=code
	$(PYTHON) -m sqlite_utils query $@ - < resources/create_table_geonames.sql
	{ $(PYTHON) -c 'print("\t".join(["geonameid","name","asciiname","alternatenames","latitude","longitude","feature_class","feature_code","country_code","cc2","admin1_code","admin2_code","admin3_code","admin4_code","population","elevation","dem","timezone","modification_date"]))'; cat data/$(COUNTRY_CODE).txt; } | $(PYTHON) -m sqlite_utils insert $@ geonames - --tsv --pk=geonameid
	
db: data/geonames.db

run: data/geonames.db | $(VENV_SENTINEL)
	$(PYTHON) -m datasette serve $< --host localhost --port 8001 --plugins-dir plugins --metadata resources/metadata.json --template-dir templates

clean:
	rm -rf data

sterilise: clean
	rm -rf downloads