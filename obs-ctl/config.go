package main

const (
	// Helm release information
	ReleaseName = "observability"
	Namespace   = "default"

	// Helm chart location
	ChartPath = "./infra/helm/observability"

	// Dashboard
	DashboardURL = "http://localhost:8082"

	// Browser
	BrowserOpen = true
)
