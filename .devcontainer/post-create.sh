#!/usr/bin/env bash

# Setup the pre-push git hook
/usr/bin/echo -e "#!/usr/bin/env bash\n\npdm check" > .git/hooks/pre-push
chmod +x .git/hooks/pre-push

# Install the development dependencies
pdm install -d
