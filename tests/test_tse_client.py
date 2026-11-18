"""Unit tests for TSE Brazil Elections parser functions."""

from __future__ import annotations

import io
import unittest

from src.tse_client import (
    build_result_url,
    parse_consolidated_results,
    parse_election_catalog,
    parse_municipalities_config,
    parse_polling_stations_stream,
)


class TseClientTest(unittest.TestCase):
    def test_build_result_url(self) -> None:
        self.assertEqual(
            build_result_url("ele2026", 6258, "br", 1),
            "https://resultados.tse.jus.br/oficial/ele2026/6258/dados/br/br-c0001-e006258-u.json",
        )
        self.assertEqual(
            build_result_url("ele2026", 6258, "sp", 1, "71072"),
            "https://resultados.tse.jus.br/oficial/ele2026/6258/dados/sp/sp71072-c0001-e006258-u.json",
        )

    def test_parse_consolidated_results(self) -> None:
        sample = {
            "ele": "6258",
            "cdabr": "br",
            "dt": "25/10/2026",
            "ht": "23:26:38",
            "s": {"pst": "100,00"},
            "v": {"tv": "124000000"},
            "carg": [
                {
                    "cd": "1",
                    "nmn": "Presidente",
                    "agr": [
                        {
                            "nm": "COLIGACAO A",
                            "par": [
                                {
                                    "sg": "PT",
                                    "cand": [
                                        {
                                            "n": "13",
                                            "sqcand": "280001",
                                            "nm": "CANDIDATO UM",
                                            "nmu": "CAND 1",
                                            "e": "s",
                                            "st": "Eleito",
                                            "vap": "64000000",
                                            "pvapn": "51,79",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        rows = parse_consolidated_results(sample, "ele2026", "https://tse.example")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["votesOrCount"], 64000000)
        self.assertAlmostEqual(rows[0]["votePct"], 51.79)
        self.assertTrue(rows[0]["elected"])

    def test_parse_catalog_and_municipalities_and_stations(self) -> None:
        cat = {
            "pl": [
                {
                    "cd": "3220",
                    "c": "ele2026",
                    "dt": "04/10/2026",
                    "e": [{"cd": "6257", "nm": "Eleicao 2026 1T", "t": "1", "abr": [{"cd": "br", "cp": [{"cd": "1", "ds": "Presidente"}]}]}],
                }
            ]
        }
        crows = parse_election_catalog(cat)
        self.assertEqual(crows[0]["electionId"], 6257)

        mun = {"abr": [{"cd": "al", "mu": [{"cd": "27855", "cdi": "2704302", "nm": "MACEIO", "z": ["0001", "0002"]}]}]}
        mrows = parse_municipalities_config(mun, "ele2026", 6258, "https://tse.example", scope="al")
        self.assertEqual(mrows[0]["ibgeCode"], "2704302")

        csv_txt = (
            "SG_UF;CD_MUNICIPIO;NM_MUNICIPIO;NR_ZONA;NR_SECAO;NR_LOCAL_VOTACAO;NM_LOCAL_VOTACAO;DS_ENDERECO;NM_BAIRRO;NR_LATITUDE;NR_LONGITUDE\n"
            "AL;27855;MACEIO;1;10;1015;ESCOLA ESTADUAL;RUA A 10;CENTRO;-9,665;-35,735\n"
            "AL;27855;MACEIO;1;11;1015;ESCOLA ESTADUAL;RUA A 10;CENTRO;-9,665;-35,735\n"
        )
        prows = parse_polling_stations_stream(io.StringIO(csv_txt), uf_filter="AL")
        self.assertEqual(len(prows), 1)
        self.assertEqual(prows[0]["sectionsCount"], 2)
        self.assertAlmostEqual(prows[0]["latitude"], -9.665)


if __name__ == "__main__":
    unittest.main()
