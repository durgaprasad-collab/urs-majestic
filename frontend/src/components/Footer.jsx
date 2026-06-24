export default function Footer() {
  const year = new Date().getFullYear()
  return (
    <footer className="site-footer">
      <img
        src="/logo.jpeg"
        alt="URS Majestic"
        className="logo logo--footer"
        width="auto"
        height="48"
      />
      <p className="footer-address">
        French Village Food Court, Thiruthani Nagar,<br />
        200 Feet Road, Pallavaram, Chennai
      </p>
      <p className="footer-veg">
        <span className="veg-mark veg-mark--light" aria-hidden="true" />
        100% Pure Vegetarian
      </p>
      <p className="footer-note">
        All menu prices are exclusive of taxes. &copy; {year} URS Majestic.
      </p>
    </footer>
  )
}
