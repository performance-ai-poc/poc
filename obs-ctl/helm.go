package main

import (
	"fmt"
	"os/exec"

	tea "github.com/charmbracelet/bubbletea"
)

// -----------------------------------------------------------------------------
// Messages
// -----------------------------------------------------------------------------

type installDoneMsg struct{}

type removeDoneMsg struct{}

type installFailedMsg struct {
	Err string
}

type removeFailedMsg struct {
	Err string
}

// -----------------------------------------------------------------------------
// Bubble Tea Commands
// -----------------------------------------------------------------------------

func installObsChartCmd(key string) tea.Cmd {
	return func() tea.Msg {
		if err := checkHelm(); err != nil {
			return installFailedMsg{
				Err: "helm is not installed or not on PATH: " + err.Error(),
			}
		}

		if err := checkCluster(); err != nil {
			return installFailedMsg{
				Err: "cannot reach Kubernetes cluster: " + err.Error(),
			}
		}

		if err := helmInstall(key); err != nil {
			return installFailedMsg{
				Err: err.Error(),
			}
		}

		return installDoneMsg{}
	}
}

func removeObsChartCmd() tea.Cmd {
	return func() tea.Msg {
		if err := checkHelm(); err != nil {
			return removeFailedMsg{
				Err: "helm is not installed or not on PATH: " + err.Error(),
			}
		}

		if err := checkCluster(); err != nil {
			return removeFailedMsg{
				Err: "cannot reach Kubernetes cluster: " + err.Error(),
			}
		}

		if err := helmUninstall(); err != nil {
			return removeFailedMsg{
				Err: err.Error(),
			}
		}

		return removeDoneMsg{}
	}
}

// -----------------------------------------------------------------------------
// Helm Helpers
// -----------------------------------------------------------------------------

func helmInstall(key string) error {
	// Key will be used in future when supported by the chart
	_ = key // Reserved for future chart support.
	cmd := exec.Command(
		HelmBinary,
		"upgrade",
		"--install",
		ReleaseName,
		ChartPath,
		"-n",
		Namespace,
		"--create-namespace",
		"--wait",
		"--timeout",
		"5m",
	)

	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("helm install failed:\n%s", string(output))
	}

	return nil
}

func helmUninstall() error {
	cmd := exec.Command(
		HelmBinary,
		"uninstall",
		ReleaseName,
		"-n",
		Namespace,
	)

	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("helm uninstall failed:\n%s", string(output))
	}

	return nil
}

// -----------------------------------------------------------------------------
// Environment Checks
// -----------------------------------------------------------------------------

func checkHelm() error {
	_, err := exec.LookPath(HelmBinary)
	return err
}

func checkCluster() error {
	cmd := exec.Command(KubectlBinary, "cluster-info")

	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("kubectl cluster check failed:\n%s", string(output))
	}

	return nil
}
