from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from python.paradox_agent.save_parser import parse_save


META = 'version="Pegasus v4.4.6" date="2202.01.01" name="Test" mods={ "Test Mod" }'

GAMESTATE = r'''
version="Pegasus v4.4.6"
date="2202.01.01"
player={ { name="unknown" country=0 } }
country={
  0={
    name={ key="TEST_EMPIRE" }
    capital=0
    starting_system=2
    owned_planets={ 0 }
    tech_status={
      technology="tech_one" level=1
      physics_queue={ { technology="tech_p" date="2202.01.01" } }
      society_queue={ }
      engineering_queue={ { technology="tech_e" date="2202.01.01" } }
      stored_techpoints={ 1 2 3 }
      alternatives={ physics={ "tech_p" } society={ "tech_s" } engineering={ "tech_e" } }
      auto_researching_physics=no auto_researching_society=no auto_researching_engineering=no
    }
    modules={ standard_economy_module={ resources={ energy=100 minerals=50 } } }
    budget={ current_month={ balance={ base={ energy=10 } upkeep={ energy=-2 minerals=-1 } } } }
    fleets_manager={ owned_fleets={ { fleet=0 } } }
    ship_design_collection={ ship_design={ 0 } }
  }
  1={ name={ key="FOREIGN_EMPIRE" } modules={ standard_economy_module={ resources={ energy=9999 } } } }
}
planets={ planet={
  4={
    colony=0 owner=0 name={ key="TEST_PLANET" } planet_class="pc_continental" planet_size=20 build_queue=0
    variables={
      paradox_agent_free_district_slots=8
      paradox_agent_free_district_city=8
      paradox_agent_free_district_generator=4
      paradox_agent_free_district_mining=3
      paradox_agent_free_district_farming=2
      paradox_agent_free_building_slots=5
      paradox_agent_can_build_building_research_lab_1=1
    }
  }
  5={ colony=1 owner=1 name={ key="FOREIGN_PLANET" } }
} }
colony={ 0={
  districts={ 0 } buildings_cache={ 0 } army_build_queue=1
  stability=75 crime=0 num_sapient_pops=4200 amenities=5000 amenities_usage=3000
  total_housing=5000 housing_usage=4200 final_designation="col_capital"
  produces={ energy=8 } upkeep={ energy=3 } profits={ energy=5 }
} }
buildings={ 0={ type="building_capital" position=0 } }
districts={ 0={ type="district_city" level=2 } }
fleet={ 0={
  name={ key="TEST_FLEET" } ships={ 0 } ship_class="shipclass_military"
  settings={ mobile=yes valid_for_combat=yes }
  movement_manager={ coordinate={ origin=2 } state=move_idle }
  military_power=12 hit_points=200
} }
ships={ 0={
  fleet=0 name={ key="TEST_SHIP" } ship_design_implementation={ design=0 }
  coordinate={ origin=2 } hitpoints=100 max_hitpoints=100 armor_hitpoints=50
  max_armor_hitpoints=50 shield_hitpoints=25 max_shield_hitpoints=25
} }
ship_design={ 0={
  name={ key="TEST_DESIGN" } auto_gen_design=no
  growth_stages={ { ship_size="corvette" section={ template="CORVETTE_MID" slot="mid" component={ slot="GUN" template="LASER" } } required_component="DRIVE" } }
} }
construction={ queue_mgr={ queues={
  0={ items={ 0 } owner=0 location={ type=2 id=4 } simultaneous=1 type=planet }
  1={ owner=1 location={ type=2 id=5 } simultaneous=1 type=planet }
  2={ owner=0 location={ type=2 id=6 } simultaneous=1 type=planet disabled=yes }
} } item_mgr={ items={ 0={ queue=0 progress=0 progress_needed=480 buildable_district={ district="district_city" planet=0 } } } } }
'''


class SaveParserTests(unittest.TestCase):
    def test_exports_only_player_owned_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            save = Path(directory) / "test.sav"
            with zipfile.ZipFile(save, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("meta", META)
                archive.writestr("gamestate", GAMESTATE)

            observation = parse_save(save)

        player = observation["player"]
        self.assertEqual(observation["save"]["version"], "Pegasus v4.4.6")
        self.assertEqual(player["country_id"], 0)
        self.assertEqual(player["capital_colony_id"], 0)
        self.assertEqual(player["capital_planet_id"], 4)
        self.assertEqual(player["resources"], {"energy": 100, "minerals": 50})
        self.assertEqual(player["monthly_balance"], {"energy": 8.0, "minerals": -1.0})
        self.assertEqual(player["research"]["researched"], [{"id": "tech_one", "level": 1}])
        self.assertEqual(
            player["research"]["active"],
            {"physics": "tech_p", "society": None, "engineering": "tech_e"},
        )
        self.assertEqual(
            player["research"]["queues"]["physics"],
            [{"technology_id": "tech_p", "selected_on": "2202.01.01"}],
        )
        self.assertEqual(player["planets"][0]["buildings"][0]["type"], "building_capital")
        self.assertEqual(player["planets"][0]["districts"][0]["level"], 2)
        self.assertEqual(player["planets"][0]["owner_id"], 0)
        self.assertEqual(
            player["planets"][0]["population"],
            {
                "sapient": 42.0,
                "unemployed": None,
                "available_jobs": None,
                "authoritative": False,
            },
        )
        self.assertEqual(
            player["planets"][0]["district_capacity"],
            {"used": 2, "available": 8, "maximum": 10, "authoritative": True},
        )
        self.assertEqual(
            player["planets"][0]["district_availability"]["district_mining"]["available"],
            3,
        )
        self.assertEqual(
            player["planets"][0]["building_capacity"],
            {"used": 1, "available": 5, "maximum": 6, "authoritative": True},
        )
        self.assertTrue(
            player["planets"][0]["building_availability"]["building_research_lab_1"]["buildable"]
        )
        self.assertFalse(player["planets"][0]["construction_queue"]["safe_to_build"])
        self.assertEqual(
            player["planets"][0]["construction_queue"]["details"]["items"][0]["buildable_district"]["district"],
            "district_city",
        )
        self.assertEqual(player["fleets"][0]["ships"][0]["design_id"], 0)
        self.assertEqual(player["ship_designs"][0]["components"][0]["template"], "LASER")
        self.assertEqual([queue["id"] for queue in player["construction_queues"]], [0])
        self.assertNotIn("FOREIGN_EMPIRE", str(observation))
        self.assertFalse(observation["visibility"]["foreign_countries_included"])


if __name__ == "__main__":
    unittest.main()
