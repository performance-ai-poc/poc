package main

import (
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// -----------------------------------------------------------------------------
// Messages sent back to the UI when Helm operations complete.
// -----------------------------------------------------------------------------

type installDoneMsg struct{}

type removeDoneMsg struct{}

// -----------------------------------------------------------------------------
// Install Observability
// -----------------------------------------------------------------------------

func installObsChartCmd(key string) tea.Cmd {
	return func() tea.Msg {

		// TODO:
		// Replace this simulation with:
		//
		// exec.Command(
		//     "helm",
		//     "install",
		//     ReleaseName,
		//     ChartPath,
		//     "-n",
		//     Namespace,
		//     "--set",
		//     "licenseKey="+key,
		// ).Run()

		time.Sleep(2 * time.Second)

		return installDoneMsg{}
	}
}

// -----------------------------------------------------------------------------
// Remove Observability
// -----------------------------------------------------------------------------

func removeObsChartCmd() tea.Cmd {
	return func() tea.Msg {

		// TODO:
		// Replace this simulation with:
		//
		// exec.Command(
		//     "helm",
		//     "uninstall",
		//     ReleaseName,
		//     "-n",
		//     Namespace,
		// ).Run()

		time.Sleep(2 * time.Second)

		return removeDoneMsg{}
	}
}
