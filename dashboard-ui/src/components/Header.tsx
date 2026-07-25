import { Search, Calendar, Bell, Plus } from "lucide-react";

interface HeaderProps {
  title: string;
}

const today = new Date().toLocaleDateString("en-US", {
  weekday: "long",
  year: "numeric",
  month: "long",
  day: "numeric",
});

export default function Header({ title }: HeaderProps) {
  return (
    <header className="topbar">
      <h1 className="topbar-title">{title}</h1>

      <div className="topbar-search">
        <Search size={18} className="icon" aria-hidden="true" />
        <input type="text" placeholder="Search..." />
      </div>

      <div className="topbar-actions">
        <div className="topbar-date">
          <Calendar size={18} className="icon" aria-hidden="true" />
          <span>{today}</span>
        </div>

        <button type="button" className="icon-btn" disabled aria-label="Notifications">
          <Bell size={18} className="icon" aria-hidden="true" />
          <span className="notification-dot" />
        </button>

        <button type="button" className="icon-btn" disabled aria-label="Add">
          <Plus size={18} className="icon" aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}