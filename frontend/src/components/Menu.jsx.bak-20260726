import { CATEGORIES } from '../data/menu'

function CategoryNav() {
  return (
    <nav className="menu-nav" aria-label="Menu categories">
      {CATEGORIES.map(cat => (
        <a key={cat.id} href={`#${cat.id}`} className="menu-nav-pill">
          {cat.name}
        </a>
      ))}
    </nav>
  )
}

function CategorySection({ cat }) {
  return (
    <div id={cat.id} className="menu-category">
      <div className="category-header">
        <h3 className="category-name">{cat.name}</h3>
        {cat.note && <span className="category-note">{cat.note}</span>}
      </div>
      <ul className="item-list" role="list">
        {cat.items.map(item => (
          <li key={item.name} className="item-row">
            <span className="item-name">
              {item.name}
              {item.bestseller && (
                <span className="item-badge" aria-label="Bestseller">Best Seller</span>
              )}
            </span>
            <span className="item-price">₹{item.price}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function Menu() {
  return (
    <section id="menu" className="menu-section">
      <div className="container">
        <div className="section-heading">
          <h2>Our Menu</h2>
          <p>All prices exclusive of taxes</p>
        </div>
        <CategoryNav />
        {CATEGORIES.map(cat => (
          <CategorySection key={cat.id} cat={cat} />
        ))}
      </div>
    </section>
  )
}
