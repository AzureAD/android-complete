"""Quick smoke test for content_sanitizer."""
from android_dri_mcp_server.content_sanitizer import (
    extract_text_preserving_links,
    sanitize_inline_images,
    truncate_entry,
    sanitize_discussion_entry,
)

# 1. HTML stripping with link preservation
html = '<p>See <a href="https://aka.ms/fix">this fix</a> for details.</p><p>Also check <b>bold text</b>.</p>'
result = extract_text_preserving_links(html)
print("Test 1 (HTML→text):", repr(result))
assert "[this fix](https://aka.ms/fix)" in result
assert "<p>" not in result
assert "<b>" not in result

# 2. Base64 image removal
text_with_img = 'Error screenshot: data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA end'
sanitized, extracted = sanitize_inline_images(text_with_img)
print("Test 2 (base64):", repr(sanitized))
assert "[IMAGE_0]" in sanitized
assert "base64" not in sanitized
assert len(extracted) == 1

# 3. Truncation
long_text = "x" * 15000
result = truncate_entry(long_text)
print("Test 3 (truncation):", len(result), "chars")
assert len(result) < 11000

# 4. Table conversion
table_html = '<table><tr><th>Col1</th><th>Col2</th></tr><tr><td>a</td><td>b</td></tr></table>'
result = extract_text_preserving_links(table_html)
print("Test 4 (table):", repr(result))
assert "Col1" in result

# 5. Full pipeline — base64 in <img src> is stripped entirely by BS4
#    (base64 step catches any remaining in raw text context)
full_html = (
    '<div>See <a href="https://dev.azure.com">DevOps</a>. '
    'Screenshot: <img src="data:image/png;base64,iVBORw0KGgo="/> '
    '</div>'
)
result = sanitize_discussion_entry(full_html)
print("Test 5 (full pipeline):", repr(result))
assert "[DevOps](https://dev.azure.com)" in result
assert "base64" not in result

# 5b. Base64 appearing as raw text (not in <img>) gets placeholders
raw_text_html = '<p>Here is data: data:image/png;base64,iVBORw0KGgoAAAANSU= inline</p>'
result = sanitize_discussion_entry(raw_text_html)
print("Test 5b (raw base64):", repr(result))
assert "[IMAGE_0]" in result
assert "base64" not in result

# 6. Empty / None gracefully
assert extract_text_preserving_links("") == ""
assert extract_text_preserving_links(None) == ""
sanitized, extracted = sanitize_inline_images("")
assert sanitized == ""

print("\nAll tests passed!")
