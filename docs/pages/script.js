document.getElementById('urlForm').addEventListener('submit', function (e) {
  e.preventDefault();

  // Get user inputs
  const resource = document.getElementById('resource').value.trim();
  const id = document.getElementById('id').value.trim();

  // Generate the URL
  const baseUrl = 'https://example.com/resource';
  const generatedUrl = `${baseUrl}/${resource}?id=${id}`;

  // Display the result
  const resultElement = document.getElementById('generatedUrl');
  resultElement.textContent = generatedUrl;
});