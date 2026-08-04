package main

import (
	"os"
	"os/exec"
)

// -----------------------------------------------------------------------------
// Dashboard Port Forward
// -----------------------------------------------------------------------------

func startDashboardPortForward() error {
	cmd := exec.Command("make", "port-forward-dashboard-ui")

	// Run from the repository root where the Makefile lives.
	cmd.Dir = ".."

	// Allow the process to keep running after this function returns.
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = nil

	// Start launches the port-forward in the background.
	// We intentionally do not wait because port-forward
	// runs until it is terminated.
	if err := cmd.Start(); err != nil {
		return err
	}

	return nil
}
