import unittest

from shelf import store


class StoreTest(unittest.TestCase):
    def test_a_new_book_is_unread(self):
        (book,) = store.add([], "Dune", "Frank Herbert")
        self.assertEqual(book, {"title": "Dune", "author": "Frank Herbert", "read": False})

    def test_marking_a_book_read(self):
        books = store.mark_read(store.add([], "Dune", "Frank Herbert"), "Dune")
        self.assertTrue(books[0]["read"])

    def test_marking_a_missing_book_fails(self):
        with self.assertRaises(KeyError):
            store.mark_read([], "Dune")

    def test_books_round_trip_through_the_file(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "shelf.json"
            store.save(store.add([], "Dune", "Frank Herbert"), path)
            self.assertEqual(store.load(path)[0]["title"], "Dune")


if __name__ == "__main__":
    unittest.main()
