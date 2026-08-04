{{- define "nightglass.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "nightglass.labels" -}}
app.kubernetes.io/name: {{ include "nightglass.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "nightglass.selector" -}}
app.kubernetes.io/name: {{ include "nightglass.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
