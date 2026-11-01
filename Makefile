.PHONY: test lint demo

test:
	python3 -m unittest discover -s tests

lint:
	claude plugin validate ./plugin --strict
	claude plugin validate . --strict

# Reads made-up data through the stand-in gh in tests/fakes.
demo:
	PATH="$$PWD/tests/fakes:$$PATH" python3 plugin/scripts/whyredci.py --repo demo/shop --run 500
