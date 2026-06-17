{{/*
Expand the name of the chart.
*/}}
{{- define "agent-load-balancer.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "agent-load-balancer.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Headless service name for per-pod bridge DNS.
*/}}
{{- define "agent-load-balancer.bridgeHeadlessServiceName" -}}
{{- printf "%s-bridge" (include "agent-load-balancer.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Stable workload resource name. Separate from fullname to allow controller-kind migration without same-name conflicts.
*/}}
{{- define "agent-load-balancer.workloadName" -}}
{{- printf "%s-workload" (include "agent-load-balancer.fullname" .) | trunc 52 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "agent-load-balancer.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "agent-load-balancer.labels" -}}
helm.sh/chart: {{ include "agent-load-balancer.chart" . }}
{{ include "agent-load-balancer.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Selector labels â€” IMMUTABLE after first deploy (name + instance ONLY, never version/chart)
*/}}
{{- define "agent-load-balancer.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agent-load-balancer.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
StatefulSet workload selector labels. These are distinct from the legacy Deployment traffic lane.
*/}}
{{- define "agent-load-balancer.workloadSelectorLabels" -}}
{{- include "agent-load-balancer.selectorLabels" . }}
agent-load-balancer.soju.dev/traffic: workload
{{- end }}

{{/*
Legacy Deployment traffic selector labels used during controller migration cutover.
*/}}
{{- define "agent-load-balancer.legacySelectorLabels" -}}
{{- include "agent-load-balancer.selectorLabels" . }}
agent-load-balancer.soju.dev/traffic: legacy
{{- end }}

{{/*
ServiceAccount name resolution
*/}}
{{- define "agent-load-balancer.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "agent-load-balancer.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Secret name â€” returns existingSecret or generated name
*/}}
{{- define "agent-load-balancer.secretName" -}}
{{- if .Values.auth.existingSecret }}
{{- .Values.auth.existingSecret }}
{{- else }}
{{- include "agent-load-balancer.fullname" . }}
{{- end }}
{{- end }}

{{/*
Database URL secret name â€” may differ from the app secret when using a dedicated external DB secret.
*/}}
{{- define "agent-load-balancer.databaseUrlSecretName" -}}
{{- if and (not .Values.postgresql.enabled) .Values.externalDatabase.existingSecret }}
{{- .Values.externalDatabase.existingSecret }}
{{- else }}
{{- include "agent-load-balancer.secretName" . }}
{{- end }}
{{- end }}

{{/*
Database URL â€” TWO code paths:
  1. postgresql.enabled: synthesize URL from sub-chart values
  2. external: use externalDatabase.url or synthesize from discrete fields
This is used in secret.yaml to populate the database-url secret key.
*/}}
{{- define "agent-load-balancer.databaseUrl" -}}
{{- if .Values.postgresql.enabled }}
{{- printf "postgresql+asyncpg://%s:%s@%s-postgresql:5432/%s" .Values.postgresql.auth.username .Values.postgresql.auth.password .Release.Name .Values.postgresql.auth.database }}
{{- else if .Values.externalDatabase.url }}
{{- .Values.externalDatabase.url }}
{{- else if and .Values.externalDatabase.host .Values.externalDatabase.user .Values.externalDatabase.database }}
{{- printf "postgresql+asyncpg://%s@%s:%v/%s" .Values.externalDatabase.user .Values.externalDatabase.host (.Values.externalDatabase.port | default 5432) .Values.externalDatabase.database }}
{{- else }}
{{- fail "No database URL source configured. Enable postgresql, set externalDatabase.url, provide externalDatabase.host/user/database, configure externalDatabase.existingSecret, auth.existingSecret, or externalSecrets.enabled." }}
{{- end }}
{{- end }}

{{/*
Migration hook phases â€” default to pre-install when DB credentials are already available without ExternalSecrets materialization.
*/}}
{{- define "agent-load-balancer.migrationHookPhases" -}}
{{- if .Values.externalSecrets.enabled -}}
post-install,pre-upgrade
{{- else if .Values.postgresql.enabled -}}
pre-upgrade
{{- else if or .Values.auth.existingSecret .Values.externalDatabase.existingSecret -}}
pre-install,pre-upgrade
{{- else -}}
post-install,pre-upgrade
{{- end -}}
{{- end }}

{{/*
Migration job service account â€” pre-install hooks cannot rely on chart-created ServiceAccounts.
Use an operator-provided existing SA when explicitly configured; otherwise fall back to default.
*/}}
{{- define "agent-load-balancer.migrationServiceAccountName" -}}{{- if and .Values.externalSecrets.enabled .Values.serviceAccount.create -}}{{- include "agent-load-balancer.serviceAccountName" . -}}{{- else if .Values.serviceAccount.name -}}{{- .Values.serviceAccount.name -}}{{- else -}}default{{- end -}}{{- end }}

{{/*
Human-readable install mode label used in NOTES and docs.
*/}}
{{- define "agent-load-balancer.installMode" -}}
{{- if .Values.postgresql.enabled -}}
bundled
{{- else if .Values.externalSecrets.enabled -}}
external-secrets
{{- else -}}
external-db
{{- end -}}
{{- end }}

{{/*
Image string â€” resolves registry/repository:tag with optional digest override
*/}}
{{- define "agent-load-balancer.image" -}}
{{- $registry := .Values.global.imageRegistry | default .Values.image.registry }}
{{- $repository := .Values.image.repository }}
{{- $tag := .Values.image.tag | default .Chart.AppVersion }}
{{- if .Values.image.digest }}
{{- printf "%s/%s@%s" $registry $repository .Values.image.digest }}
{{- else }}
{{- printf "%s/%s:%s" $registry $repository $tag }}
{{- end }}
{{- end }}

{{/*
Merged nodeSelector: global.nodeSelector + local nodeSelector (local wins).
*/}}
{{- define "agent-load-balancer.nodeSelector" -}}
{{- $merged := mustMergeOverwrite (deepCopy (.Values.global.nodeSelector | default dict)) (.Values.nodeSelector | default dict) -}}
{{- if $merged }}
{{- toYaml $merged }}
{{- end }}
{{- end -}}

{{/*
Global-only nodeSelector for hooks/tests so app-specific placement does not block installs.
*/}}
{{- define "agent-load-balancer.globalNodeSelector" -}}
{{- with (.Values.global.nodeSelector | default dict) }}
{{- toYaml . }}
{{- end }}
{{- end -}}
