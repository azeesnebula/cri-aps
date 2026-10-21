# Pin the planning engine version used by this workspace.
FROM ghcr.io/frepple/frepple-community@sha256:66ea8f7928aa4f9e87b718c75728a0b9d7f03bed9b8cac61cc4c61dfe046c963
USER root
COPY --chown=frepple:frepple djangosettings.py /etc/frepple/djangosettings.py
# Keep source outside the upstream /etc/frepple volume so rebuilds deploy it.
COPY --chown=frepple:frepple cri_ui /opt/cri-pumps/cri_ui
COPY --chown=frepple:frepple cri-logo.gif /opt/cri-pumps/cri_ui/static/cri_ui/cri-logo.gif
# Preserve the existing workbook import/export and empty-data fixes.
COPY freppledb/execute/management/commands/empty.py freppledb/execute/management/commands/exportworkbook.py /usr/share/frepple/venv/lib/python3.12/site-packages/freppledb/execute/management/commands/
COPY freppledb/execute/templates/commands/empty.html freppledb/execute/templates/commands/exportworkbook.html /usr/share/frepple/venv/lib/python3.12/site-packages/freppledb/execute/templates/commands/
COPY freppledb/execute/utils.py freppledb/execute/views.py /usr/share/frepple/venv/lib/python3.12/site-packages/freppledb/execute/
COPY docker/cri-entrypoint.sh /cri-entrypoint.sh
RUN sed -i 's/\r$//' /cri-entrypoint.sh && chmod +x /cri-entrypoint.sh
ENTRYPOINT ["/cri-entrypoint.sh"]
