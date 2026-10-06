# DEVELOPMENT IMAGE. Not deployable as-is -- see the CMD note at the bottom.
FROM node:22-alpine

WORKDIR /app

# `npm ci` rather than `npm install`, and the lockfile is NOT optional.
#
# `npm install` resolves against the registry and may pick versions the
# lockfile does not name, so the image can differ from what CI tested while
# both report success. `npm ci` installs exactly the lockfile and fails if it
# is missing or out of step with package.json -- which is the behaviour you
# want in an image build: loud, not creative.
#
# Measured rather than assumed: `npm ci` was run against this lockfile from a
# clean clone in both of this project's environments and installed cleanly,
# followed by `npm run lint` and `npm run build`.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173
# The Vite DEV SERVER, bound to every interface.
#
# This is a development convenience and must not face the internet. The dev
# server exists to serve unbundled modules with a hot-reload websocket and a
# file-serving root; it is not hardened, not minified, and not what the
# `build` script produces.
#
# A deployable image is a different file and a different shape: a build stage
# running `npm ci && npm run build`, then a minimal stage serving the static
# `dist/` output. It is deliberately NOT written here, because neither
# environment this repository has been built in has docker, so such a file
# could not be built, run or verified -- and an unverified production
# Dockerfile that looks right is worse than an honest development one.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
