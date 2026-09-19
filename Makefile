.PHONY: aggregate-figures test validate

aggregate-figures:
	python figures/build_public_figures.py --output-dir build/figures

test:
	python -m unittest discover -s tests -v

validate:
	python tests/validate_repository.py

