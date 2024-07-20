SHELL := /usr/bin/bash

BOLD != tput bold
NORMAL != tput sgr0
YELLOW != tput setaf 3

.PHONY: lint static-analysis run-unit-tests check-formatting format \
				extract-strings new-translation update-translations \
				export-translations check-strings check-translations update-doc-stubs \
				generate-docs check-docs release

lint:
	@/usr/bin/echo -e '${BOLD}${YELLOW}Checking for common issues...${NORMAL}'
	@ruff check src/ tests/

static-analysis:
	@/usr/bin/echo -e '${BOLD}${YELLOW}Running static code analysis...${NORMAL}'
	@mypy src/ tests/

run-unit-tests:
	@/usr/bin/echo -e '${BOLD}${YELLOW}Running Python tests...${NORMAL}'
	pytest -vvra

check-formatting:
	@/usr/bin/echo -e '${BOLD}${YELLOW}Checking Python source files for formatting' \
										'issues...${NORMAL}'
	@black src/alfred tests/ --check

format:
	@/usr/bin/echo -e '${BOLD}${YELLOW}Formatting Python source files...${NORMAL}'
	@black src/alfred tests/

extract-strings:
	@/usr/bin/echo -e '${BOLD}${YELLOW}Extracting strings for translation..${NORMAL}'
	@mkdir locales 2>/dev/null || true
	@find src/ -iname '*.py' | xargs xgettext --force-po -d alfred -o locales/alfred.pot --from-code UTF-8

new-translation:
	@/usr/bin/echo -e '${BOLD}${YELLOW}Creating a new translation target...${NORMAL}'
	@locale="${LOCALE}" \
	&& mkdir -p locales/"$${locale:0:2}"/LC_MESSAGES \
	&& msginit -l "$${locale}" -o locales/$${locale:0:2}/LC_MESSAGES/alfred.po -i locales/alfred.pot --no-translator

update-translations: extract-strings
	@/usr/bin/echo -e '${BOLD}${YELLOW}Updating translation files with new strings...${NORMAL}'
	@for d in locales/*/; do \
		msgmerge --backup none --update "$${d}"LC_MESSAGES/alfred.po locales/alfred.pot; \
	done

export-translations:
	@/usr/bin/echo -e '${BOLD}${YELLOW}Exporting translations...${NORMAL}'
	@for d in locales/*/; do \
		msgfmt --check -o "$${d}"LC_MESSAGES/alfred.mo "$${d}"LC_MESSAGES/alfred; \
	done

check-strings:
	@/usr/bin/echo -e '${BOLD}${YELLOW}Checking for new strings for translation...${NORMAL}'
	@diff  -I 'POT-Creation-Date.*' locales/alfred.pot <(find src/ -iname '*.py' | xargs xgettext --force-po -d alfred -o - --from-code UTF-8)
	@echo "No new translation strings found."

check-translations: check-strings
	@/usr/bin/echo -e '${BOLD}${YELLOW}Checking for translations that need to be updated with new strings...${NORMAL}'
	@for d in locales/*/; do \
		diff -I 'POT-Creation-Date.*' "$${d}"LC_MESSAGES/alfred.po <(msgmerge "$${d}"LC_MESSAGES/alfred.po locales/alfred.pot -o -); \
	done

update-doc-stubs:
	@/usr/bin/echo -e '${BOLD}${YELLOW}Updating Sphinx documentation stubs...${NORMAL}'
	@sphinx-apidoc -f -o docs/source src/alfred

generate-docs: update-doc-stubs
	@/usr/bin/echo -e '${BOLD}${YELLOW}Building HTML Sphinx documentation...${NORMAL}'
	@${MAKE} ${MFLAGS} -C docs clean html SPHINXOPTS="-W --keep-going"

define _CHECK_DOC_STUBS
python <<'EOF'
import io, logging, pathlib, sphinx.ext.apidoc
stream = io.StringIO()
logging.basicConfig(stream=stream, level=logging.INFO)
sphinx.ext.apidoc.main(["--dry-run", "-o", "docs/source", "src/alfred"])
if "Would create file" in stream.getvalue():
	for ln in stream.getvalue().split("\n"):
		fn = ln.replace("INFO:sphinx.sphinx.ext.apidoc:Would create file ", "")[:-1]
		if not (pathlib.Path.cwd() / fn).exists():
			print(
				f'Document stubs are not up to date.\n'
				'Please run "pdm update-doc-stubs".'
			)
			raise SystemExit(1)
EOF
endef
export CHECK_DOC_STUBS = $(value _CHECK_DOC_STUBS)

check-docs:
	@/usr/bin/echo -e '${BOLD}${YELLOW}Checking that the documentation stubs are up to date...${NORMAL}'
	@eval "$$CHECK_DOC_STUBS"
	@echo "No out of date files found."

	@/usr/bin/echo -e '${BOLD}${YELLOW}Checking that Sphinx documentation can be built...${NORMAL}'
	@${MAKE} ${MFLAGS} -C docs clean html SPHINXOPTS="-W --keep-going"

release: VERSION=$(shell \
	(git describe --tags --match="v2[0-9][0-9][0-9].[0-9][0-9].*" HEAD 2>/dev/null || date "+%Y.%m.0") \
		| awk -F '.' \
		"{if (\$$0 ~ /v$$(date "+%Y.%m.")/)` \
		` {print \"v$$(date "+%Y.%m.")\" (\$$3 + 1)}` \
		` else {print \"v\" strftime(\"%Y.%m.0\")}}" \
)

release:
	@/usr/bin/echo -e "${BOLD}${YELLOW}Beginning release process for version ${VERSION}${NORMAL}"

	@/usr/bin/echo -e "${BOLD}Updating source files to include new version${NORMAL}"
	@echo "Updating src/alfred/__init__.py..."
	@sed -i "s/^__version__: str = .*/__version__: str = \"${VERSION}\"/" src/alfred/__init__.py
	@echo "Updating pyproject.toml..."
	@sed -i "s/^version = \".*\"/version = \"${VERSION}\"/" pyproject.toml

	@/usr/bin/echo -e "${BOLD}Creating release commit${NORMAL}"
	@git add src/alfred/__init__.py pyproject.toml
	@git commit -m "Releasing ${VERSION}"

	@/usr/bin/echo -e "${BOLD}Tagging release${NORMAL}"
	@git tag -a "${VERSION}" -m "${VERSION}"

	@/usr/bin/echo -e "${BOLD}Pushing release to origin${NORMAL}"
	@git push origin main
	@git push origin tag "${VERSION}"
