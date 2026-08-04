package main

import "github.com/pkg/browser"

// -----------------------------------------------------------------------------
// Browser Helpers
// -----------------------------------------------------------------------------

func openDashboard(url string) {
	if !BrowserOpen {
		return
	}

	_ = browser.OpenURL(url)
}
