SHELL := /usr/bin/bash

BOLD != tput bold
NORMAL != tput sgr0
YELLOW != tput setaf 3

.PHONY: lint static-analysis run-unit-tests check-formatting format \
				extract-strings new-translation update-translations \
				export-translations update-doc-stubs generate-docs release \

lint:
	@/usr/bin/echo -e '${YELLOW}Checking for common issues...${NORMAL}'
	@ruff check src/ tests/

static-analysis:
	@/usr/bin/echo -e '${YELLOW}Running static code analysis...${NORMAL}'
	@mypy src/ tests/

run-unit-tests:
	@/usr/bin/echo -e '${YELLOW}Running Python tests...${NORMAL}'
	pytest -vvra

check-formatting:
	@/usr/bin/echo -e '${YELLOW}Checking Python source files for formatting' \
										'issues...${NORMAL}'
	@black src/alfred tests/ --check

format:
	@/usr/bin/echo -e '${YELLOW}Formatting Python source files...${NORMAL}'
	@black src/alfred tests/

extract-strings:
	@mkdir locales 2>/dev/null || true
	@find src/ -iname '*.py' | xargs xgettext --force-po -d alfred -o locales/alfred.pot --from-code UTF-8

new-translation:
	@locale="${LOCALE}" \
	&& mkdir -p locales/"$${locale:0:2}"/LC_MESSAGES \
	&& msginit -l "$${locale}" -o locales/$${locale:0:2}/LC_MESSAGES/alfred.po -i locales/alfred.pot --no-translator

update-translations: extract-strings
	@for d in locales/*/; do \
		msgmerge --update "$${d}"LC_MESSAGES/alfred.po locales/alfred.pot; \
		rm "$${d}"LC_MESSAGES/alfred.po~; \
	done

export-translations:
	@for d in locales/*/; do \
		msgfmt --check -o "$${d}"LC_MESSAGES/alfred.mo "$${d}"LC_MESSAGES/alfred; \
	done

update-doc-stubs:
	@sphinx-apidoc -f -o docs/source src/alfred

generate-docs: update-doc-stubs
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
