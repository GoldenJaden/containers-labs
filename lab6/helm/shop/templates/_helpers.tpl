{{- define "shop.labels" -}}
app.kubernetes.io/name: shop
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
lab_release: {{ .Release.Name }}
{{- end }}
{{- define "shop.annotations" -}}
{{- if .Values.mesh.enabled }}
{{- toYaml .Values.mesh.annotations }}
{{- else }}
linkerd.io/inject: disabled
{{- end }}
{{- end }}
