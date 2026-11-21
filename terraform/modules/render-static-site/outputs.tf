output "id" {
  description = "Render id of the static site (srv-...)."
  value       = render_static_site.this.id
}

output "url" {
  description = "Public onrender.com URL of the static site."
  value       = render_static_site.this.url
}
