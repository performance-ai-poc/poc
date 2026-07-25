const PRODUCTS = ["Guardian AI", "IDE AI", "Adapt AI", "Performance AI"];
const SETTINGS = ["Settings", "User", "Support", "API"];

interface SidebarProps {
  activeProduct: string;
}

export default function Sidebar({ activeProduct }: SidebarProps) {
  return (
    <aside className="sidebar">

      <div className="sidebar-nav">
        <div className="sidebar-section">
          <div className="sidebar-heading">Products</div>
          <nav>
            {PRODUCTS.map((product) => (
              <div
                key={product}
                className={`sidebar-item${product === activeProduct ? " active" : ""}`}
              >
                {product}
              </div>
            ))}
          </nav>
        </div>

        <div className="sidebar-section">
          <div className="sidebar-heading">Settings</div>
          <nav>
            {SETTINGS.map((item) => (
              <div key={item} className="sidebar-item">
                {item}
              </div>
            ))}
          </nav>
        </div>
      </div>
    </aside>
  );
}