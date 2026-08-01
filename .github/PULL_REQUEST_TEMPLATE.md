<!--
  Thanks for contributing to paperless-mcp!
  Please keep this template intact and just fill in the sections below.
-->

## Proposed change

<!--
  Describe what your change does and why it is needed. If it fixes a bug or
  implements a feature request, link the issue under "Related issues" below.
-->

## Type of change

<!-- Please check exactly one box. If more than one applies, consider splitting the PR. -->

- [ ] Bugfix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that changes existing behavior)
- [ ] Code quality, refactor or performance (no functional change)
- [ ] Documentation
- [ ] CI, tooling or dependency update

## Breaking change

<!--
  Only fill this in if you checked "Breaking change" above: what breaks, how to
  migrate, and why the change is worth it. This text ends up in the release
  notes, so please write it for users of the server, not for the maintainer.

  Note that the tool surface is the public API here: renaming a tool, dropping
  a parameter or changing a return shape breaks every MCP client out there.
-->

## Related issues

- Fixes #
- Related to #

## Checklist

<!--
  Tick what applies. Not sure about something? Open the PR anyway and ask.
-->

- [ ] I understand the code I am submitting and can explain how it works.
- [ ] AI and agentic coding tools are very welcome here. If I used one, I have read and reviewed its entire output myself and take responsibility for it.
- [ ] `uv run pytest -x -q` passes locally and coverage stays at 80 % or above.
- [ ] `prek run --all-files` passes (ruff, mypy, codespell, yamllint).
- [ ] Tests have been added or updated for the changed behavior.
- [ ] (OPTIONAL) New or changed tools are registered in `src/paperless_mcp/tools/__init__.py` and covered by `tests/test_tool_registration.py`.
- [ ] (OPTIONAL) `README.md` has been updated if the tool surface or the configuration changed.
- [ ] (OPTIONAL) The change was tried against a live Paperless-ngx instance, or it cannot affect live API interaction.

<!--
  Reviews can take a few days: paperless-mcp is a private side project. But every
  pull request gets a reply. Thanks for your patience!
-->
