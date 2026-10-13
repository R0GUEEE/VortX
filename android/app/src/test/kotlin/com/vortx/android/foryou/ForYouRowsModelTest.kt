package com.vortx.android.foryou

import com.vortx.android.model.MediaType
import com.vortx.android.model.MetaItem
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

/** The same scenarios as Apple `app/Tests/ForYouRowsModelTests.swift`, against a recording TMDB fake. */
@OptIn(ExperimentalCoroutinesApi::class)
class ForYouRowsModelTest {
    private val calls = mutableListOf<String>()
    private var empty = false
    private var rows = ForYouRows()

    private fun model(scope: TestScope) = ForYouRowsModel(
        scope = scope,
        ruleTitles = { movie, tv ->
            calls += "rule:${movie ?: "-"}|${tv ?: "-"}"
            if (empty) emptyList() else (1..5).map { MetaItem(id = "tt${calls.size}$it", type = MediaType.MOVIE, name = "T$it") }
        },
        personTitles = { id ->
            calls += "person:$id"
            if (empty) emptyList() else (1..3).map { MetaItem(id = "tp$id$it", type = MediaType.MOVIE, name = "P$it") }
        },
        today = { LocalDate.of(2026, 10, 13) },
        onRows = { rows = it },
    )

    @Test fun `rows follow the plan, refilter without refetching and retry an empty fetch`() = runTest(StandardTestDispatcher()) {
        val model = model(this)
        val interests = ForYouInterests(genres = listOf("scifi"), people = listOf(ForYouPerson(7, "Someone")), decided = true)
        val pinned = SmartCollection.make("Weekend dramas", SmartCollectionRules(genres = listOf("drama"), hideWatched = true), home = true)
        val libraryOnly = SmartCollection.make("Library only", SmartCollectionRules(), home = false)

        model.refresh("A", interests, listOf(pinned, libraryOnly), emptySet(), emptySet(), true)
        advanceUntilIdle()
        assertEquals(listOf("forYou.genre.scifi", "forYou.person.7"), rows.interest.map { it.plan.id })
        assertEquals(ForYouReason.INTERESTS, rows.interest[0].plan.reason)
        assertEquals(listOf("Weekend dramas"), rows.collections.map { it.collection.name })
        val fetched = calls.size
        assertEquals(3, fetched)

        val firstScifi = rows.interest[0].items[0].id
        val firstDrama = rows.collections[0].items[0].id
        model.refresh("A", interests, listOf(pinned, libraryOnly), setOf(firstScifi), setOf(firstDrama), true)
        advanceUntilIdle()
        assertEquals(fetched, calls.size)
        assertFalse(rows.interest[0].items.any { it.id == firstScifi })
        assertFalse(rows.collections[0].items.any { it.id == firstDrama })

        model.refresh("A", interests, listOf(pinned.copy(name = "Renamed")), emptySet(), emptySet(), true)
        advanceUntilIdle()
        assertEquals(fetched, calls.size)
        assertEquals(listOf("Renamed"), rows.collections.map { it.collection.name })

        model.refresh("A", interests, emptyList(), emptySet(), emptySet(), false)
        assertTrue(rows.interest.isEmpty() && rows.collections.isEmpty())

        empty = true
        model.refresh("B", interests, emptyList(), emptySet(), emptySet(), true)
        advanceUntilIdle()
        val afterEmpty = calls.size
        assertTrue(rows.interest.isEmpty())
        empty = false
        model.refresh("B", interests, emptyList(), emptySet(), emptySet(), true)
        advanceUntilIdle()
        assertEquals(afterEmpty + 2, calls.size)
        assertEquals(2, rows.interest.size)
    }
}
