package main

import (
	"fmt"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// -----------------------------------------------------------------------------
// UI Styling
// -----------------------------------------------------------------------------

var (
	titleStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("#7D56F4")).
			Padding(0, 1)

	infoStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#04B575"))

	warnStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#FF5F56"))

	boxStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("#7D56F4")).
			Padding(1, 2)
)

// -----------------------------------------------------------------------------
// UI Modes
// -----------------------------------------------------------------------------

type mode int

const (
	modeMenu mode = iota
	modeInputKey
	modeInstalling
	modeRemoving
	modeComplete
)

// -----------------------------------------------------------------------------
// Bubble Tea Model
// -----------------------------------------------------------------------------

type model struct {
	mode      mode
	textInput textinput.Model
	spinner   spinner.Model
	statusMsg string
	dashURL   string
	success   bool
}

// -----------------------------------------------------------------------------
// Initial Model
// -----------------------------------------------------------------------------

func initialModel() model {
	ti := textinput.New()
	ti.Placeholder = "XXXX-XXXX-XXXX-XXXX"
	ti.CharLimit = 36
	ti.Width = 38

	s := spinner.New()
	s.Spinner = spinner.Dot

	return model{
		mode:      modeMenu,
		textInput: ti,
		spinner:   s,
		dashURL:   DashboardURL,
	}
}

// -----------------------------------------------------------------------------
// Bubble Tea Lifecycle
// -----------------------------------------------------------------------------

func (m model) Init() tea.Cmd {
	return nil
}

// -----------------------------------------------------------------------------
// Update
// -----------------------------------------------------------------------------

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {

	switch msg := msg.(type) {

	case tea.KeyMsg:

		switch msg.String() {

		case "ctrl+c", "q":
			return m, tea.Quit

		case "1":
			if m.mode == modeMenu {
				m.mode = modeInputKey
				m.statusMsg = ""
				m.textInput.SetValue("")
				m.textInput.Focus()
				return m, textinput.Blink
			}

		case "2":
			if m.mode == modeMenu {
				m.mode = modeRemoving
				return m, tea.Batch(
					m.spinner.Tick,
					removeObsChartCmd(),
				)
			}

		case "enter":
			if m.mode == modeInputKey {

				key := m.textInput.Value()

				if key != LicenseKey {
					m.statusMsg = "Invalid license key. Please enter the correct key."
					return m, nil
				}

				m.mode = modeInstalling

				return m, tea.Batch(
					m.spinner.Tick,
					installObsChartCmd(key),
				)
			}

		}

	case installDoneMsg:
		m.success = true
		m.mode = modeComplete
		m.statusMsg = "Observability Plane installed successfully in default namespace!"

		go openDashboard(m.dashURL)

		return m, nil

	case removeDoneMsg:
		m.success = true
		m.mode = modeComplete
		m.statusMsg = "Observability Plane removed successfully from default namespace!"
		return m, nil

	case installFailedMsg:
		m.success = false
		m.mode = modeComplete
		m.statusMsg = msg.Err
		return m, nil

	case removeFailedMsg:
		m.success = false
		m.mode = modeComplete
		m.statusMsg = msg.Err
		return m, nil

	}

	var cmd tea.Cmd

	if m.mode == modeInputKey {
		m.textInput, cmd = m.textInput.Update(msg)
	} else if m.mode == modeInstalling || m.mode == modeRemoving {
		m.spinner, cmd = m.spinner.Update(msg)
	}

	return m, cmd
}

// -----------------------------------------------------------------------------
// View
// -----------------------------------------------------------------------------

func (m model) View() string {

	switch m.mode {

	case modeMenu:

		return boxStyle.Render(
			fmt.Sprintf(
				"%s\n\n[1] Install Observability Plane (default namespace)\n[2] Remove Observability Plane (default namespace)\n\nPress 'q' to exit.",
				titleStyle.Render("Observability Plane Manager"),
			),
		)

	case modeInputKey:
		return boxStyle.Render(
			fmt.Sprintf(
				"Enter Observability License Key:\n\n%s\n\n%s\n\n(Press Enter to submit)",
				m.textInput.View(),
				warnStyle.Render(m.statusMsg),
			),
		)

	case modeInstalling:

		return boxStyle.Render(
			fmt.Sprintf(
				"%s Deploying Observability Helm release to default namespace...",
				m.spinner.View(),
			),
		)

	case modeRemoving:

		return boxStyle.Render(
			fmt.Sprintf(
				"%s Uninstalling Observability Helm release from default namespace...",
				m.spinner.View(),
			),
		)

	case modeComplete:

		style := infoStyle
		if !m.success {
			style = warnStyle
		}

		if m.success {
			return boxStyle.Render(
				fmt.Sprintf(
					"%s\n\nDashboard Endpoint: %s\n\nPress 'q' to exit.",
					style.Render(m.statusMsg),
					m.dashURL,
				),
			)
		}

		return boxStyle.Render(
			fmt.Sprintf(
				"%s\n\nPress 'q' to exit.",
				style.Render(m.statusMsg),
			),
		)

	}

	return ""
}
