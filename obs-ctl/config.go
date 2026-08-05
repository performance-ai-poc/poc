package main

const (
	// Executables
	HelmBinary    = "helm"
	KubectlBinary = "kubectl"

	// Helm release information
	ReleaseName = "observability"
	Namespace   = "default"

	// Helm chart location
	ChartPath = "../infra/helm/observability"

	// Dashboard
	DashboardURL = "http://dashboard.local"

	// Default license
	LicenseKey = "0000"

	// Browser
	BrowserOpen = true
)
