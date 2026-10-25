.PHONY: install ingest build report serve test all

install:
	pip install -r requirements.txt

ingest:
	python -m ingest.extract

build:
	python build.py

report:
	python build.py --report

serve:
	python -m http.server 8000 -d docs

test:
	python -m unittest discover -s tests
	node tests/test_score.js

all: ingest build
