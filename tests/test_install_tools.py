import unittest
from unittest.mock import patch

from scripts import install_tools


class InstallerTests(unittest.TestCase):
    @patch.dict("os.environ", {"GITHUB_TOKEN": "fixture-token"})
    @patch("scripts.install_tools.urlopen")
    def test_ci_authentication_is_only_sent_to_the_https_github_api(self, urlopen):
        for url, authenticated in (
            ("https://api.github.com/repos/denoland/deno/releases/latest", True),
            ("https://github.com/denoland/deno/releases/download/test/tools.zip", False),
            ("https://release-assets.githubusercontent.com/tools.zip", False),
            ("https://api.github.com.example.com/tools.zip", False),
            ("http://api.github.com/repos/denoland/deno/releases/latest", False),
        ):
            with self.subTest(url=url):
                install_tools._fetch(url)
                request = urlopen.call_args.args[0]
                self.assertEqual(request.get_header("Authorization"), "Bearer fixture-token" if authenticated else None)


if __name__ == "__main__":
    unittest.main()
